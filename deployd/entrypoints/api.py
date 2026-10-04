"""FastAPI HTTP entrypoint for the event ingestion pipeline.

Investigations go through ``InvestigationOrchestrator`` — the three-tier gate.
The LLM is reachable only when the incident graph holds a causal chain AND the
hybrid retriever finds a historical runbook above threshold (Tier 3, FULL).
Follow-up chat reuses that grounded agent session; for Tier 1/2 investigations
the chat answers deterministically and never calls the LLM.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import uuid
from dataclasses import asdict, dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any

import groq
from agno.exceptions import AgnoError
from deployd.adapters.incoming.http_event_adapter import HttpEventAdapter, RawTelemetryEvent
from deployd.adapters.outgoing.ai.agno_agent import MAX_FOLLOW_UP_TURNS, AgnoGroqAgent
from deployd.adapters.outgoing.registry.json_component_repository import (
    JSONComponentRepository,
)
from deployd.adapters.outgoing.registry.json_topology import load_topology
from deployd.adapters.outgoing.vector_store.bm25_index import BM25RunbookIndex
from deployd.adapters.outgoing.vector_store.chroma_client import ChromaRunbookClient
from deployd.adapters.outgoing.vector_store.graph_index import GraphIndex
from deployd.adapters.outgoing.vector_store.graph_store import GraphStore, RunbookStructure
from deployd.adapters.outgoing.vector_store.hybrid_retriever import HybridRetriever
from deployd.adapters.outgoing.vector_store.runbook_repository import JSONRunbookRepository
from deployd.application.dtos.diagnosis import DiagnosisTier
from deployd.application.dtos.investigation_request import InvestigationRequest
from deployd.application.orchestrators.investigation_orchestrator import (
    InvestigationOrchestrator,
)
from deployd.application.use_cases.correlate_events import (
    CorrelateEventsUseCase,
    compute_incident_severity,
)
from deployd.application.use_cases.incident_lifecycle import (
    GetIncidentUseCase,
    IncidentLifecycleUseCase,
    ListIncidentsUseCase,
)
from deployd.domain.causal.causal_engine import CausalEngine
from deployd.domain.causal.config import CorrelationConfig
from deployd.domain.graph.graph import IncidentGraph
from deployd.domain.graph.node import GraphNode
from deployd.domain.health.process_health import ProcessHealthFSM
from deployd.domain.health.process_state import ProcessHealthStatus
from deployd.entrypoints.decision_trace import build_decision_trace
from deployd.infrastructure.persistence.sqlite_incident_repository import (
    SQLiteIncidentRepository,
)
from deployd.infrastructure.streaming.sliding_window import SlidingWindow
from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

log = logging.getLogger(__name__)

# Errors an LLM run can raise: model/provider failures, missing agent, invalid
# structured output.  The investigation fails closed on any of them.
_AGENT_ERRORS = (AgnoError, groq.GroqError, RuntimeError, TypeError, ValueError)

# FSM parameters — same defaults as BuildInvestigation.
_FSM_RECOVERY_WINDOW = timedelta(seconds=300)
_FSM_MAX_RESTARTS = 3
_FSM_RESTART_WINDOW = timedelta(seconds=120)


def _repo_root() -> Path:
    """Walk up from this file to find the repo root (contains pyproject.toml)."""
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "pyproject.toml").exists():
            return parent
    raise RuntimeError("Could not locate repo root (no pyproject.toml in ancestors)")


_DATA_DIR = _repo_root() / "data"

# Persistence
_incident_repo = SQLiteIncidentRepository()
_lifecycle = IncidentLifecycleUseCase(_incident_repo)
_list_incidents = ListIncidentsUseCase(_incident_repo)
_get_incident = GetIncidentUseCase(_incident_repo)

# In-memory pipeline
_state_lock = threading.Lock()
_chat_lock = threading.Lock()
_graph = IncidentGraph()
_window = SlidingWindow(window_seconds=300)

with (_DATA_DIR / "correlation_config.json").open(encoding="utf-8") as f:
    _config = CorrelationConfig(**json.load(f))

_topology = load_topology(_DATA_DIR / "components.json")
_correlate = CorrelateEventsUseCase(
    graph=_graph, event_window=_window, config=_config, topology=_topology
)
_adapter = HttpEventAdapter()

# Investigation state
# Chat messages are flat str→str dicts so they persist as Incident.chat_history.
# Keys: role (user|agent|system), kind (user|event|diagnosis|followup|
# deterministic|gate|error), content, plus kind-specific extras.
_chat_history: list[dict[str, str]] = []
_decision_trace: dict[str, Any] | None = None
_session: dict[str, Any] | None = None  # grounded agent session (Tier 3 only)
_investigating = False
_auto_diagnosed_incident_id: str | None = None


# Retrieval + agent (built lazily: embedding model and LLM client are heavy)


@dataclass
class _RetrievalStack:
    retriever: HybridRetriever
    chroma: ChromaRunbookClient
    repo: JSONRunbookRepository


_retrieval: _RetrievalStack | None = None
_agent: AgnoGroqAgent | None = None


def _get_retrieval() -> _RetrievalStack:
    global _retrieval  # noqa: PLW0603
    if _retrieval is None:
        repo = JSONRunbookRepository(_DATA_DIR / "runbooks")
        runbooks = repo.list_all()
        chroma = ChromaRunbookClient(persist_directory=str(_DATA_DIR / "chroma"))
        for rb in runbooks:  # upsert: idempotent, keeps the index in sync with the JSON
            chroma.index_runbook(rb.runbook_id, rb.summary, {"tags": ",".join(rb.tags)})
        bm25 = BM25RunbookIndex()
        bm25.build([(rb.runbook_id, rb.summary) for rb in runbooks])
        store = GraphStore()
        for rb in runbooks:
            store.add(
                RunbookStructure(
                    runbook_id=rb.runbook_id,
                    causal_chain=tuple(rb.causal_chain),
                    affected_components=frozenset(rb.affected_components),
                )
            )
        retriever = HybridRetriever(chroma=chroma, bm25=bm25, graph_index=GraphIndex(store))
        _retrieval = _RetrievalStack(retriever=retriever, chroma=chroma, repo=repo)
    return _retrieval


def _get_agent() -> AgnoGroqAgent | None:
    """The Tier-3 agent, or None when GROQ_API_KEY is not configured."""
    global _agent  # noqa: PLW0603
    if not os.getenv("GROQ_API_KEY", "").strip():
        return None
    if _agent is None:
        stack = _get_retrieval()
        registry = JSONComponentRepository(
            components_file=_DATA_DIR / "components.json",
            constraints_file=_DATA_DIR / "compatibility_constraints.json",
        )
        _agent = AgnoGroqAgent(
            chroma_client=stack.chroma, runbook_repo=stack.repo, component_registry=registry
        )
    return _agent


# Graph helpers


def _graph_snapshot() -> dict[str, Any]:
    return {
        "nodes": [
            {
                "id": str(node.node_id),
                "label": node.event.description,
                "type": node.event.event_type.value,
                "severity": node.event.severity.value,
                "source": node.event.related_component,
                "timestamp": node.event.timestamp.isoformat(),
            }
            for node in _graph.nodes
        ],
        "edges": [
            {
                "source": str(edge.source),
                "target": str(edge.target),
                "relationship": edge.edge_type.value,
                "confidence": edge.confidence,
                "rule_id": edge.rule_id,
            }
            for edge in _graph.edges
        ],
    }


def _longest_chain() -> list[GraphNode]:
    engine = CausalEngine(_graph)
    chains = [c for root in _graph.get_root_nodes() for c in engine.causal_chain(root.node_id)]
    return max(chains, key=len, default=[])


def _chain_rules(chain: list[GraphNode]) -> list[str]:
    """Rule IDs of the edges along a chain, in order."""
    rules: list[str] = []
    for src, dst in zip(chain, chain[1:], strict=False):
        edge = next(
            (e for e in _graph.outgoing_edges(src.node_id) if e.target == dst.node_id), None
        )
        if edge is not None:
            rules.append(edge.rule_id or edge.edge_type.value)
    return rules


def _chain_text(chain: list[GraphNode]) -> str:
    return " → ".join(
        f"{n.event.event_type.value} ({n.event.related_component or '?'})" for n in chain
    )


def _fsm_state(component: str) -> ProcessHealthStatus:
    fsm = ProcessHealthFSM(
        recovery_window=_FSM_RECOVERY_WINDOW,
        max_restart_count=_FSM_MAX_RESTARTS,
        restart_time_window=_FSM_RESTART_WINDOW,
    )
    events = sorted(
        (n.event for n in _graph.nodes if n.event.related_component == component),
        key=lambda e: e.timestamp,
    )
    for event in events:
        fsm.process_event(event)
    return fsm.state


def _provider_hint(detail: str) -> str:
    if "rate_limit" in detail or "Request too large" in detail:
        return "The LLM provider rate limit was hit — wait a minute and try again."
    return "The LLM provider returned an error."


# Chat helpers


def _post(role: str, kind: str, content: str, **extra: str) -> None:
    _chat_history.append({"role": role, "kind": kind, "content": content, **extra})


def _persist_chat() -> None:
    current = _incident_repo.get_current()
    if current is None:
        return
    current.chat_history = list(_chat_history)
    if current.root_cause_summary is None:
        summary = next(
            (m["content"] for m in _chat_history if m["kind"] in ("diagnosis", "deterministic")),
            None,
        )
        if summary:
            current.root_cause_summary = summary[:300]
    _incident_repo.save(current)


# App

app = FastAPI(title="DeployD API", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# Event ingestion


@app.post("/api/v1/events", status_code=200)  # type: ignore[misc]
async def receive_event(
    raw: RawTelemetryEvent, background_tasks: BackgroundTasks
) -> dict[str, Any]:
    global _auto_diagnosed_incident_id  # noqa: PLW0603
    try:
        core_event = _adapter.translate(raw)
        with _state_lock:
            _correlate.ingest(core_event)
            incident = _lifecycle.ensure_open(core_event)
            _lifecycle.update_severity(core_event.severity)

            current_global_severity = compute_incident_severity(_graph)
            if current_global_severity == "Critical" and _auto_diagnosed_incident_id != str(
                incident.id
            ):
                _auto_diagnosed_incident_id = str(incident.id)
                background_tasks.add_task(_run_investigation, str(incident.id))

            return {
                "status": "ok",
                "event_id": str(core_event.event_id),
                "provisional_severity": core_event.severity.value,
                "incident_id": str(incident.id),
            }
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


# Live state


@app.get("/api/v1/state")  # type: ignore[misc]
async def get_state() -> dict[str, Any]:
    with _state_lock:
        current = _incident_repo.get_current()
        return {
            "tracker_status": compute_incident_severity(_graph),
            "incident_id": str(current.id) if current else None,
            "graphs": _graph_snapshot(),
            "chat_history": list(_chat_history),
            "decision_trace": _decision_trace,
            "session": _session,
            "investigating": _investigating,
        }


# Investigation (three-tier gate)


def _run_investigation(target_incident_id: str) -> None:
    """Run the three-tier gate on the live graph; the LLM is reached only in Tier 3."""
    global _decision_trace, _session, _investigating  # noqa: PLW0603
    with _state_lock:
        chain = _longest_chain()
        if not chain:
            return

        _investigating = True
    try:
        with _state_lock:
            current = _incident_repo.get_current()
            if not current or str(current.id) != target_incident_id:
                return

            component = chain[0].event.related_component or "unknown-component"
            chain_types = tuple(n.event.event_type.value for n in chain)
            components = frozenset(
                n.event.related_component for n in chain if n.event.related_component
            )
            query = f"{component}: " + "; ".join(
                n.event.description or n.event.event_type.value for n in chain
            )
            _post(
                "system",
                "event",
                f"Investigation triggered on **{component}**: the incident reached CRITICAL "
                f"with a {len(chain) - 1}-hop causal chain. Running the three-tier gate…",
            )
            fsm_state_val = _fsm_state(component)
            rules_fired_val = _chain_rules(chain)
            graph_snapshot = _graph  # Or just pass the shared ref, orchestrator only reads

        stack = _get_retrieval()
        retrieval, breakdown = stack.retriever.retrieve_scored(
            query, causal_chain=chain_types, components=components
        )
        agent = _get_agent()
        request = InvestigationRequest(
            component=component,
            graph=graph_snapshot,
            fsm_state=fsm_state_val,
            retrieval_result=retrieval,
        )

        result = None
        llm_error: str | None = None
        try:
            result = InvestigationOrchestrator(agent=agent).run(request)
            tier = result.tier
        except _AGENT_ERRORS as exc:
            # Only Tier 3 can raise: gate passed, but the agent is missing or failed.
            # Fail closed — show the deterministic chain, never an unvalidated answer.
            log.exception("Tier-3 agent run failed")
            tier = DiagnosisTier.FULL
            llm_error = str(exc) if agent is not None else None

        llm_called = tier is DiagnosisTier.FULL and agent is not None

        with _state_lock:
            current = _incident_repo.get_current()
            if not current or str(current.id) != target_incident_id:
                return

            _decision_trace = build_decision_trace(
                tier=tier,
                component=component,
                query=query,
                chain=chain_types,
                rules_fired=rules_fired_val,
                candidates=retrieval.candidates,
                breakdown={rid: asdict(scores) for rid, scores in breakdown.items()},
                threshold=retrieval.confidence_threshold,
                agent_available=agent is not None,
                llm_called=llm_called,
                tokens_used=agent.last_token_usage if llm_called and agent else None,
                llm_error=llm_error,
            )

            diagnosis = result.structured_diagnosis if result else None
            if diagnosis is not None and agent is not None and agent.last_session_id:
                _session = {
                    "id": agent.last_session_id,
                    "component": component,
                    "turn": 0,
                    "max_turns": MAX_FOLLOW_UP_TURNS,
                }
                _post(
                    "agent",
                    "diagnosis",
                    diagnosis.root_cause,
                    confidence=diagnosis.confidence,
                    reasoning=diagnosis.reasoning,
                    recommendation=diagnosis.recommendation,
                    evidence=",".join(diagnosis.evidence_references),
                    tokens=str(_decision_trace["tokens_used"] or ""),
                )
            else:
                reason = (
                    _provider_hint(llm_error)
                    if llm_error
                    else _decision_trace["reason_llm_skipped"] or ""
                )
                summary = (
                    result.remediation.summary
                    if result
                    else (
                        "The gate allowed a grounded diagnosis, but the agent could not produce a "
                        "validated answer. Showing the deterministic evidence instead."
                    )
                )
                _post(
                    "agent",
                    "deterministic",
                    summary,
                    tier=tier.value,
                    chain=_chain_text(chain),
                    reason=reason,
                )
            _persist_chat()
    finally:
        with _state_lock:
            _investigating = False


# Chat (multi-turn, grounded session only)


class _ChatRequest(BaseModel):
    prompt: str


@app.post("/api/v1/chat")  # type: ignore[misc]
def chat(request: _ChatRequest) -> dict[str, str]:
    """Follow-up question.  Sync on purpose: FastAPI runs it in a worker thread,
    so a slow LLM call does not block ``/state`` polling."""
    prompt = request.prompt.strip()
    if not prompt:
        raise HTTPException(status_code=422, detail="Empty prompt")
    
    with _chat_lock:
        with _state_lock:
            _post("user", "user", prompt)

            if _investigating:
                _post("system", "gate", "An investigation is running — ask again in a moment.")
                _persist_chat()
                return {"status": "ok"}
            elif _decision_trace is None:
                _post(
                    "system",
                    "gate",
                    "No investigation yet. DeployD opens one automatically when the incident "
                    "reaches CRITICAL. Until then there is no evidence to ground an answer, "
                    "so the LLM is not called.",
                )
                _persist_chat()
                return {"status": "ok"}
            elif _session is None and _decision_trace["llm_error"]:
                _post(
                    "system",
                    "error",
                    "The gate allowed the agent for this investigation, but the grounded diagnosis "
                    f"failed, so there is no agent session to continue. "
                    f"{_provider_hint(_decision_trace['llm_error'])}",
                )
                _persist_chat()
                return {"status": "ok"}
            elif _session is None:
                _post(
                    "system",
                    "gate",
                    f"This investigation is **{_decision_trace['tier']}** "
                    f"({_decision_trace['reason_llm_skipped']}). "
                    "Follow-up with the agent is only available for grounded (FULL) "
                    "investigations, so the LLM is not called.",
                )
                _persist_chat()
                return {"status": "ok"}
            
            session_snapshot = _session.copy()
            current_incident = _incident_repo.get_current()
            incident_id = str(current_incident.id) if current_incident else None

        _answer_follow_up(prompt, session_snapshot, incident_id)

    return {"status": "ok"}


def _answer_follow_up(prompt: str, session: dict[str, Any], target_incident_id: str | None) -> None:
    agent = _get_agent()
    if agent is None:
        with _state_lock:
            _post("system", "gate", "The agent is no longer configured (GROQ_API_KEY missing).")
            _persist_chat()
        return
    try:
        answer = agent.follow_up(session["id"], prompt)
    except _AGENT_ERRORS as exc:
        log.exception("Follow-up failed")
        with _state_lock:
            _post("system", "error", f"{_provider_hint(str(exc))}\n\n`{str(exc)[:240]}`")
            _persist_chat()
        return
    
    with _state_lock:
        current_incident = _incident_repo.get_current()
        if not current_incident or str(current_incident.id) != target_incident_id:
            return
        
        if _session and _session["id"] == session["id"]:
            _session["turn"] = min(_session["turn"] + 1, _session["max_turns"])
            _post(
                "agent",
                "followup",
                answer.root_cause,
                turn=str(_session["turn"]),
                tokens=str(agent.last_token_usage or ""),
            )
            _persist_chat()


# ── Reset ─────────────────────────────────────────────────────────────────────


@app.post("/api/v1/reset")  # type: ignore[misc]
async def reset_state() -> dict[str, Any]:
    global _graph, _window, _correlate, _chat_history  # noqa: PLW0603
    global _decision_trace, _session, _auto_diagnosed_incident_id  # noqa: PLW0603

    with _state_lock:
        closed = _lifecycle.close_current(
            graph_snapshot=_graph_snapshot(),
            chat_history=list(_chat_history),
            root_cause_summary=next(
                (m["content"][:300] for m in _chat_history if m["role"] == "agent"), None
            ),
        )

        _graph = IncidentGraph()
        _window = SlidingWindow(window_seconds=300)
        _correlate = CorrelateEventsUseCase(
            graph=_graph, event_window=_window, config=_config, topology=_topology
        )
        _chat_history = []
        _decision_trace = None
        _session = None
        _auto_diagnosed_incident_id = None

    return {
        "status": "reset",
        "closed_incident_id": str(closed.id) if closed else None,
    }


# ── Incident history ──────────────────────────────────────────────────────────


@app.get("/api/v1/incidents")  # type: ignore[misc]
async def list_incidents() -> list[dict[str, Any]]:
    incidents = _list_incidents.execute()
    return [
        {
            "id": str(inc.id),
            "opened_at": inc.opened_at.isoformat(),
            "resolved_at": inc.resolved_at.isoformat() if inc.resolved_at else None,
            "status": inc.status,
            "peak_severity": inc.peak_severity.value,
            "node_count": len(inc.graph_snapshot.get("nodes", [])),  # type: ignore[arg-type]
            "duration_seconds": inc.duration_seconds,
            "root_cause_summary": inc.root_cause_summary,
        }
        for inc in incidents
    ]


@app.get("/api/v1/incidents/{incident_id}")  # type: ignore[misc]
async def get_incident(incident_id: uuid.UUID) -> dict[str, Any]:
    incident = _get_incident.execute(incident_id)
    if incident is None:
        raise HTTPException(status_code=404, detail="Incident not found")
    return {
        "id": str(incident.id),
        "opened_at": incident.opened_at.isoformat(),
        "resolved_at": incident.resolved_at.isoformat() if incident.resolved_at else None,
        "status": incident.status,
        "peak_severity": incident.peak_severity.value,
        "graph_snapshot": incident.graph_snapshot,
        "chat_history": incident.chat_history,
        "root_cause_summary": incident.root_cause_summary,
        "duration_seconds": incident.duration_seconds,
    }

"""FastAPI HTTP entrypoint for the event ingestion pipeline."""

from __future__ import annotations

import logging
import uuid
from typing import Any

from deployd.adapters.incoming.http_event_adapter import HttpEventAdapter, RawTelemetryEvent
from deployd.adapters.outgoing.ai.agno_agent import AgnoGroqAgent
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
from deployd.domain.graph.graph import IncidentGraph
from deployd.infrastructure.persistence.sqlite_incident_repository import (
    SQLiteIncidentRepository,
)
from deployd.infrastructure.streaming.sliding_window import SlidingWindow
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

log = logging.getLogger(__name__)

# ── Persistence ───────────────────────────────────────────────────────────────
_incident_repo = SQLiteIncidentRepository()
_lifecycle = IncidentLifecycleUseCase(_incident_repo)
_list_incidents = ListIncidentsUseCase(_incident_repo)
_get_incident = GetIncidentUseCase(_incident_repo)

# ── In-memory pipeline ────────────────────────────────────────────────────────
_graph = IncidentGraph()
_window = SlidingWindow(window_seconds=300)
_correlate = CorrelateEventsUseCase(graph=_graph, event_window=_window)
_adapter = HttpEventAdapter()
_agent: AgnoGroqAgent | None = None

_chat_history: list[dict[str, str]] = []
_last_session_id: str | None = None


def _get_agent() -> AgnoGroqAgent:
    global _agent  # noqa: PLW0603
    if _agent is None:
        _agent = AgnoGroqAgent()
    return _agent


def _graph_snapshot() -> list[dict[str, Any]]:
    return [
        {
            "id": str(node.node_id),
            "label": node.event.description,
            "type": node.event.event_type.value,
            "severity": node.event.severity.value,
            "source": node.event.related_component,
            "timestamp": node.event.timestamp.isoformat(),
        }
        for node in _graph.nodes
    ]


app = FastAPI(title="DeployD API", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Event ingestion ───────────────────────────────────────────────────────────


@app.post("/api/v1/events", status_code=200)  # type: ignore[misc]
async def receive_event(raw: RawTelemetryEvent) -> dict[str, Any]:
    try:
        core_event = _adapter.translate(raw)
        _correlate.ingest(core_event)
        incident = _lifecycle.ensure_open(core_event)
        _lifecycle.update_severity(core_event.severity)
        return {
            "status": "ok",
            "event_id": str(core_event.event_id),
            "provisional_severity": core_event.severity.value,
            "incident_id": str(incident.id),
        }
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


# ── Live state ────────────────────────────────────────────────────────────────


@app.get("/api/v1/state")  # type: ignore[misc]
async def get_state() -> dict[str, Any]:
    nodes = _graph_snapshot()
    edges = [
        {
            "source": str(edge.source),
            "target": str(edge.target),
            "relationship": edge.edge_type.value,
            "confidence": edge.confidence,
            "rule_id": edge.rule_id,
        }
        for edge in _graph.edges
    ]
    current = _incident_repo.get_current()
    return {
        "tracker_status": compute_incident_severity(_graph),
        "incident_id": str(current.id) if current else None,
        "graphs": {"nodes": nodes, "edges": edges},
        "chat_history": _chat_history,
    }


# ── Chat ──────────────────────────────────────────────────────────────────────


@app.post("/api/v1/chat")  # type: ignore[misc]
async def chat(request: _ChatRequest) -> dict[str, str]:
    global _last_session_id  # noqa: PLW0603

    _chat_history.append({"role": "user", "content": request.prompt})

    try:
        if not _graph.nodes:
            reply = (
                "No incident data available yet. "
                "Please send some telemetry events to `/api/v1/events` first."
            )
        elif _last_session_id:
            agent = _get_agent()
            diagnosis = agent.follow_up(_last_session_id, request.prompt)
            _last_session_id = agent.last_session_id
            reply = (
                f"**Root Cause:** {diagnosis.root_cause}\n\n"
                f"**Confidence:** {diagnosis.confidence}\n\n"
                f"**Recommendation:** {diagnosis.recommendation}"
            )
        else:
            engine = CausalEngine(_graph)
            root_nodes = _graph.get_root_nodes()
            component = (
                (root_nodes[0].event.related_component or "unknown-component")
                if root_nodes
                else "unknown-component"
            )
            chains: list[list[Any]] = []
            for root in root_nodes:
                chains.extend(engine.causal_chain(root.node_id))

            agent = _get_agent()
            diagnosis = agent.diagnose(
                component=component,
                causal_chains=chains,
                candidates=[],
            )
            _last_session_id = agent.last_session_id
            reply = (
                f"**Root Cause:** {diagnosis.root_cause}\n\n"
                f"**Confidence:** {diagnosis.confidence}\n\n"
                f"**Recommendation:** {diagnosis.recommendation}"
            )
    except Exception as exc:
        log.exception("Agent interaction failed")
        reply = f"Agent error: {exc}"

    _chat_history.append({"role": "agent", "content": reply})

    # Persist chat to open incident
    _root_summary = next((m["content"] for m in _chat_history if m["role"] == "agent"), None)
    current = _incident_repo.get_current()
    if current:
        current.chat_history = list(_chat_history)
        if _root_summary and current.root_cause_summary is None:
            current.root_cause_summary = _root_summary[:300]
        _incident_repo.save(current)

    return {"status": "ok"}


# ── Reset ─────────────────────────────────────────────────────────────────────


@app.post("/api/v1/reset")  # type: ignore[misc]
async def reset_state() -> dict[str, Any]:
    global _graph, _window, _correlate, _chat_history, _last_session_id, _agent  # noqa: PLW0603

    closed = _lifecycle.close_current(
        graph_snapshot=_graph_snapshot(),
        chat_history=list(_chat_history),
        root_cause_summary=next(
            (m["content"][:300] for m in _chat_history if m["role"] == "agent"), None
        ),
    )

    _graph = IncidentGraph()
    _window = SlidingWindow(window_seconds=300)
    _correlate = CorrelateEventsUseCase(graph=_graph, event_window=_window)
    _chat_history = []
    _last_session_id = None
    _agent = None

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
            "node_count": len(inc.graph_snapshot),
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


class _ChatRequest(BaseModel):
    prompt: str

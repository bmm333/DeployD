"""FastAPI HTTP entrypoint for the event ingestion pipeline."""

from __future__ import annotations

import logging
from typing import Any

from deployd.adapters.incoming.http_event_adapter import HttpEventAdapter, RawTelemetryEvent
from deployd.adapters.outgoing.ai.agno_agent import AgnoGroqAgent
from deployd.application.use_cases.correlate_events import (
    CorrelateEventsUseCase,
    compute_incident_severity,
)
from deployd.domain.causal.causal_engine import CausalEngine
from deployd.domain.graph.graph import IncidentGraph
from deployd.infrastructure.streaming.sliding_window import SlidingWindow
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

log = logging.getLogger(__name__)


_graph = IncidentGraph()
_window = SlidingWindow(window_seconds=300)  # 5-minute observation window
_correlate = CorrelateEventsUseCase(graph=_graph, event_window=_window)
_adapter = HttpEventAdapter()
_agent: AgnoGroqAgent | None = None


def _get_agent() -> AgnoGroqAgent:
    global _agent  # noqa: PLW0603
    if _agent is None:
        _agent = AgnoGroqAgent()
    return _agent


_chat_history: list[dict[str, str]] = []
_last_session_id: str | None = None

app = FastAPI(title="DeployD API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.post("/api/v1/events", status_code=200)  # type: ignore[misc]
async def receive_event(raw: RawTelemetryEvent) -> dict[str, Any]:
    try:
        core_event = _adapter.translate(raw)
        _correlate.ingest(core_event)
        return {
            "status": "ok",
            "event_id": str(core_event.event_id),
            "provisional_severity": core_event.severity.value,
        }
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/api/v1/state")  # type: ignore[misc]
async def get_state() -> dict[str, Any]:
    nodes = [
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

    return {
        "tracker_status": compute_incident_severity(_graph),
        "graphs": {"nodes": nodes, "edges": edges},
        "chat_history": _chat_history,
    }


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
            # First message — build causal chains from the graph and diagnose
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
    return {"status": "ok"}


@app.post("/api/v1/reset")  # type: ignore[misc]
async def reset_state() -> dict[str, str]:
    """Reset all in-memory state (used between demo scenarios)."""
    global _graph, _window, _correlate, _chat_history, _last_session_id, _agent  # noqa: PLW0603
    _graph = IncidentGraph()
    _window = SlidingWindow(window_seconds=300)
    _correlate = CorrelateEventsUseCase(graph=_graph, event_window=_window)
    _chat_history = []
    _last_session_id = None
    _agent = None  # force re-init of agent on next chat
    return {"status": "reset"}


class _ChatRequest(BaseModel):
    prompt: str

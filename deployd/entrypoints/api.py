"""
DeployD FastAPI entrypoint.

Wires together:
  - HttpEventAdapter  : raw telemetry → CoreEvent
  - EventCorrelator   : CoreEvent → IncidentGraph (CAUSAL edges, emergent severity)
  - AgnoGroqAgent     : IncidentGraph → grounded AI diagnosis
"""

from __future__ import annotations

import logging
from typing import Any

from deployd.adapters.incoming.http_event_adapter import HttpEventAdapter, RawTelemetryEvent
from deployd.adapters.outgoing.ai.agno_agent import AgnoGroqAgent
from deployd.domain.causal.event_correlator import make_correlator
from deployd.domain.graph.graph import IncidentGraph
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Application state (in-memory for demo — swap with a persistent store later)
# ---------------------------------------------------------------------------

_graph = IncidentGraph()
_correlator = make_correlator(graph=_graph, window_seconds=300)  # 5-minute window
_adapter = HttpEventAdapter()
_agent = AgnoGroqAgent()
_chat_history: list[dict[str, str]] = []

# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

app = FastAPI(title="DeployD API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@app.post("/api/v1/events", status_code=200)  # type: ignore[misc]
async def receive_event(raw: RawTelemetryEvent) -> dict[str, Any]:
    """
    Ingest a raw telemetry event from any external service.

    The emitter does NOT set severity or incident context — it only reports
    what happened locally. DeployD infers severity and causal relationships
    from the accumulated window of events.
    """
    try:
        core_event = _adapter.translate(raw)
        _correlator.ingest(core_event)
        return {
            "status": "ok",
            "event_id": str(core_event.event_id),
            "provisional_severity": core_event.severity.value,
        }
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/api/v1/state")  # type: ignore[misc]
async def get_state() -> dict[str, Any]:
    """
    Return the current IncidentGraph and chat history.

    The global ``tracker_status`` is derived from graph topology:
    - No causal chains → Healthy
    - Chain length 1–2 → Degrading
    - Chain length 3+  → Critical
    """
    nodes = []
    for node in _graph.nodes:
        evt = node.event
        nodes.append(
            {
                "id": str(node.node_id),
                "label": evt.description,
                "type": evt.event_type.value,
                "severity": evt.severity.value,
                "source": evt.related_component,
                "timestamp": evt.timestamp.isoformat(),
            }
        )

    edges = []
    for edge in _graph.edges:
        edges.append(
            {
                "source": str(edge.source),
                "target": str(edge.target),
                "relationship": edge.edge_type.value,
                "confidence": edge.confidence,
                "rule_id": edge.rule_id,
            }
        )

    # Emergent status: based on graph depth, not on any single event label
    max_chain_depth = _compute_max_chain_depth()
    if max_chain_depth == 0:
        tracker_status = "Healthy"
    elif max_chain_depth <= 2:
        tracker_status = "Degrading"
    else:
        tracker_status = "Critical"

    return {
        "tracker_status": tracker_status,
        "graphs": {"nodes": nodes, "edges": edges},
        "chat_history": _chat_history,
    }


@app.post("/api/v1/chat")  # type: ignore[misc]
async def chat(request: _ChatRequest) -> dict[str, str]:
    """
    Natural-language interaction with the SRE Agent.

    The agent receives the current incident context automatically.
    """
    _chat_history.append({"role": "user", "content": request.prompt})

    try:
        base_agent = _agent._create_structured_agent()
        response = base_agent.run(request.prompt)

        if hasattr(response.content, "root_cause"):
            reply = (
                f"**Root Cause:** {response.content.root_cause}\n\n"
                f"**Recommendation:** {response.content.recommendation}"
            )
        elif isinstance(response.content, str):
            reply = response.content
        else:
            reply = str(response.content)

    except Exception as exc:
        log.exception("Agent run failed")
        reply = f"Agent error: {exc}"

    _chat_history.append({"role": "agent", "content": reply})
    return {"status": "ok"}


@app.post("/api/v1/reset")  # type: ignore[misc]
async def reset_state() -> dict[str, str]:
    """Reset in-memory state (useful between demo scenarios)."""
    global _graph, _correlator, _chat_history  # noqa: PLW0603
    _graph = IncidentGraph()
    _correlator = make_correlator(graph=_graph, window_seconds=300)
    _chat_history = []
    return {"status": "reset"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _compute_max_chain_depth() -> int:
    """
    Compute the length of the longest causal chain in the graph.

    We do a simple BFS/DFS from each root node (no incoming CAUSAL edges)
    following CAUSAL edges only. This is O(N+E) and acceptable for demo scale.
    """
    from deployd.domain.graph.edge_type import EdgeType

    if not _graph.nodes:
        return 0

    # Build adjacency from CAUSAL edges
    adj: dict[str, list[str]] = {}
    for edge in _graph.edges:
        if edge.edge_type == EdgeType.CAUSAL:
            adj.setdefault(str(edge.source), []).append(str(edge.target))

    if not adj:
        return 0  # Nodes exist but no causal links yet → single-node anomalies only

    visited_depth: dict[str, int] = {}

    def dfs(node_id: str, depth: int) -> int:
        if node_id in visited_depth:
            return visited_depth[node_id]
        visited_depth[node_id] = depth
        children = adj.get(node_id, [])
        if not children:
            return depth
        return max(dfs(child, depth + 1) for child in children)

    # Start DFS from all nodes that have outgoing edges but no incoming CAUSAL edges
    incoming_targets = {str(e.target) for e in _graph.edges if e.edge_type == EdgeType.CAUSAL}
    roots = [str(n.node_id) for n in _graph.nodes if str(n.node_id) not in incoming_targets]

    if not roots:
        # Cycle or no root — fall back to full scan
        roots = list(adj.keys())

    return max(dfs(r, 1) for r in roots)


class _ChatRequest(BaseModel):
    prompt: str

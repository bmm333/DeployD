import uuid
from datetime import datetime, timezone

import requests
from demo.scenarios import ScenarioSnapshot
from deployd.application.dtos.investigation_request import InvestigationRequest
from deployd.application.dtos.retrieval import RetrievalResult
from deployd.domain.entities.core_event import CoreEvent
from deployd.domain.graph.edge import GraphEdge
from deployd.domain.graph.edge_type import EdgeType
from deployd.domain.graph.graph import IncidentGraph
from deployd.domain.graph.node import GraphNode
from deployd.domain.health.process_state import ProcessHealthStatus


def fetch_live_scenario() -> ScenarioSnapshot:
    resp = requests.get("http://localhost:8081/api/v1/state").json()
    graph = IncidentGraph()
    events = []

    for n in resp["graphs"]["nodes"]:
        evt = CoreEvent(
            id=n["id"],
            type=n["type"],
            timestamp=datetime.now(timezone.utc).isoformat(),
            source=n.get("source", "unknown"),
            description=n["label"],
            severity=n["severity"],
            metadata={},
        )
        events.append(evt)
        try:
            graph.add_node(GraphNode(node_id=uuid.UUID(n["id"]), event=evt))
        except Exception:
            graph.add_node(GraphNode(node_id=uuid.uuid4(), event=evt))

    for e in resp["graphs"]["edges"]:
        import contextlib

        with contextlib.suppress(Exception):
            graph.add_edge(
                GraphEdge(
                    source=uuid.UUID(e["source"]),
                    target=uuid.UUID(e["target"]),
                    edge_type=EdgeType.CAUSAL,
                    confidence=e["confidence"],
                    rule_id="live-rule",
                )
            )

    status_map = {
        "HEALTHY": ProcessHealthStatus.HEALTHY,
        "DEGRADED": ProcessHealthStatus.DEGRADED,
        "CRITICAL": ProcessHealthStatus.CRASHING,
    }

    return ScenarioSnapshot(
        name="LIVE: SaaS Simulation",
        description=f"Live simulation data. {resp['raw_events_count']} raw events ingested.",
        component="postgres-db (Live)",
        events=events,
        graph=graph,
        fsm_transitions=[],
        fsm_final_state=status_map.get(resp["tracker_status"], ProcessHealthStatus.HEALTHY),
        retrieval_result=RetrievalResult(
            candidates=[], confidence_threshold=0.8, has_strong_match=True
        ),
        runbook_details=[],
        request=InvestigationRequest(
            component="postgres-db",
            fsm_state=status_map.get(resp["tracker_status"], ProcessHealthStatus.HEALTHY),
            graph=graph,
            retrieval_result=RetrievalResult(
                candidates=[], confidence_threshold=0.8, has_strong_match=True
            ),
        ),
    )

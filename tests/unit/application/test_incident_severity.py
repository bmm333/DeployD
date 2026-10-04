"""Incident severity from the longest CAUSAL chain, measured in hops (edges)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from deployd.application.use_cases.correlate_events import (
    CRITICAL_MIN_HOPS,
    compute_incident_severity,
)
from deployd.domain.entities.core_event import CoreEvent, CoreEventType, Severity
from deployd.domain.graph.edge import GraphEdge
from deployd.domain.graph.edge_type import EdgeType
from deployd.domain.graph.graph import IncidentGraph
from deployd.domain.graph.node import GraphNode

T0 = datetime(2026, 9, 30, 10, 0, tzinfo=timezone.utc)


def _graph(
    nodes: int, edges: list[tuple[int, int]], edge_type: EdgeType = EdgeType.CAUSAL
) -> IncidentGraph:
    graph = IncidentGraph()
    created = [
        GraphNode(
            event=CoreEvent(
                event_type=CoreEventType.STATE_CHANGE,
                severity=Severity.ERROR,
                timestamp=T0 + timedelta(seconds=i),
                description=f"n{i}",
            )
        )
        for i in range(nodes)
    ]
    for node in created:
        graph.add_node(node)
    for src, dst in edges:
        graph.add_edge(
            GraphEdge(
                source=created[src].node_id,
                target=created[dst].node_id,
                edge_type=edge_type,
                confidence=0.8,
            )
        )
    return graph


def test_critical_threshold_is_two_hops() -> None:
    assert CRITICAL_MIN_HOPS == 2


def test_empty_graph_is_healthy() -> None:
    assert compute_incident_severity(IncidentGraph()) == "Healthy"


def test_one_hop_chain_is_degrading() -> None:
    assert compute_incident_severity(_graph(2, [(0, 1)])) == "Degrading"


def test_two_hop_chain_is_critical() -> None:
    assert compute_incident_severity(_graph(3, [(0, 1), (1, 2)])) == "Critical"


def test_branching_counts_the_longest_path_not_the_number_of_edges() -> None:
    # 0→1, 0→2, 0→3: three edges, but every path is a single hop.
    assert compute_incident_severity(_graph(4, [(0, 1), (0, 2), (0, 3)])) == "Degrading"


def test_only_causal_edges_count() -> None:
    temporal = _graph(3, [(0, 1), (1, 2)], edge_type=EdgeType.TEMPORAL)
    assert compute_incident_severity(temporal) != "Critical"

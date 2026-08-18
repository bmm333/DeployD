"""Tests for IncidentGraph, _make_event helper to create a CoreEvent"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from deployd.domain.entities.core_event import CoreEvent, CoreEventType, Severity
from deployd.domain.graph.edge import GraphEdge
from deployd.domain.graph.edge_type import EdgeType
from deployd.domain.graph.graph import (
    DuplicateEdgeError,
    DuplicateNodeError,
    IncidentGraph,
    NodeNotFoundError,
    SelfLoopError,
)
from deployd.domain.graph.node import GraphNode

BASE_TIME = datetime(2026, 8, 18, 12, 0, 0, tzinfo=timezone.utc)


def _make_event(
    *,
    event_type: CoreEventType = CoreEventType.PROCESS_CRASH,
    severity: Severity = Severity.CRITICAL,
    component: str = "api-gateway",
    timestamp: datetime = BASE_TIME,
    metadata: dict | None = None,
) -> CoreEvent:
    return CoreEvent(
        event_id=uuid.uuid4(),
        timestamp=timestamp,
        event_type=event_type,
        severity=severity,
        component=component,
        metadata=metadata or {},
    )


def _make_node(**overrides) -> GraphNode:
    return GraphNode(event=_make_event(**overrides))


def _make_edge(
    source: GraphNode,
    target: GraphNode,
    *,
    edge_type: EdgeType = EdgeType.CAUSAL,
    confidence: float = 0.9,
    rule_id: str | None = "rule-1",
) -> GraphEdge:
    return GraphEdge(
        source=source.event_id,
        target=target.event_id,
        edge_type=edge_type,
        confidence=confidence,
        rule_id=rule_id,
    )


# mutations


class TestAddNode:
    def test_add_node_succeeds(self):
        graph = IncidentGraph()
        node = _make_node()
        graph.add_node(node)
        assert graph.get_node(node.node_id) == node

    def test_add_duplicate_node_raises(self):
        graph = IncidentGraph()
        node = _make_node()
        graph.add_node(node)
        with pytest.raises(DuplicateNodeError):
            graph.add_node(node)

    def test_add_node_with_same_id_diff_content_still_raise(self):
        graph = IncidentGraph()
        shared_id = uuid.uuid4()
        event_a = _make_event().mode_copy(update={"event_id": shared_id})
        event_b = _make_event(component="other-service").model_copy(update={"event_id": shared_id})
        graph.add_node(GraphNode(event=event_a))
        with pytest.raises(DuplicateNodeError):
            graph.add_node(GraphNode(event=event_b))


class TestAddEdge:
    def test_add_edge_succeeds(self):
        graph = IncidentGraph()
        a, b = _make_node(), _make_node()
        graph.add_node(a)
        graph.add_node(b)
        edge = _make_edge(a, b)
        graph.add_edge(edge)
        assert edge in graph.outgoing_edges(a.node_id)
        assert edge in graph.incoming_edges(b.node_id)

    def test_self_loop_raises(self):
        graph = IncidentGraph()
        a = _make_node()
        graph.add_node(a)
        edge = GraphEdge(
            source=a.node_id, target=a.node_id, edge_type=EdgeType.CAUSAL, confidence=0.9
        )
        with pytest.raises(SelfLoopError):
            graph.add_edge(edge)

    def test_edge_with_missing_node_raises(self):
        graph = IncidentGraph()
        b = _make_node()
        graph.add_node(b)
        edge = GraphEdge(
            source=uuid.uuid4(),  # not in graph
            target=b.node_id,
            edge_type=EdgeType.CAUSAL,
            confidence=0.9,
        )
        with pytest.raises(NodeNotFoundError):
            graph.add_edge(edge)

    def test_edge_with_mising_target_raises(self):
        graph = IncidentGraph()
        a = _make_node()
        graph.add_node(a)
        edge = GraphEdge(
            source=a.node_id,
            target=uuid.uuid4(),  # not in graph
            edge_type=EdgeType.CAUSAL,
            confidence=0.9,
        )
        with pytest.raises(NodeNotFoundError):
            graph.add_edge(edge)

    def test_duplicate_edge_same_rule_raises(self):
        graph = IncidentGraph()
        a, b = _make_node(), _make_node()
        graph.add_node(a)
        graph.add_node(b)
        graph.add_edge(_make_edge(a, b, rule_id="rule-1"))
        with pytest.raises(DuplicateEdgeError):
            graph.add_edge(_make_edge(a, b, rule_id="rule-1"))

    def test_same_pair_different_rule_is_allowed(self):
        """Two independent rules agreeing/disagreeing on the same
        relationship is signal, not noise — must NOT raise."""
        graph = IncidentGraph()
        a, b = _make_node(), _make_node()
        graph.add_node(a)
        graph.add_node(b)
        graph.add_edge(_make_edge(a, b, rule_id="rule-1"))
        graph.add_edge(_make_edge(a, b, rule_id="rule-2"))
        assert len(graph.outgoing_edges(a.node_id)) == 2

    def test_same_pair_different_edge_type_is_allowed(self):
        graph = IncidentGraph()
        a, b = _make_node(), _make_node()
        graph.add_node(a)
        graph.add_node(b)
        graph.add_edge(_make_edge(a, b, edge_type=EdgeType.CAUSAL, rule_id="rule-1"))
        graph.add_edge(_make_edge(a, b, edge_type=EdgeType.TEMPORAL, rule_id="rule-1"))
        assert len(graph.outgoing_edges(a.node_id)) == 2


class TestGetNode:
    def test_get_missing_node_raises(self):
        graph = IncidentGraph()
        with pytest.raises(NodeNotFoundError):
            graph.get_node(uuid.uuid4())


class TestTopologyQueries:
    def test_root_nodes_have_no_incoming_edges(self):
        graph = IncidentGraph()
        deploy = _make_node(event_type=CoreEventType.DEPLOY_STARTED)
        crash = _make_node()
        graph.add_node(deploy)
        graph.add_node(crash)
        graph.add_edge(_make_edge(deploy, crash))
        assert graph.get_root_nodes() == [deploy]

    def test_leaf_nodes_have_no_outgoing_edges(self):
        graph = IncidentGraph()
        deploy = _make_node(event_type=CoreEventType.DEPLOY_STARTED)
        crash = _make_node()
        graph.add_node(deploy)
        graph.add_node(crash)
        graph.add_edge(_make_edge(deploy, crash))
        assert graph.get_leaf_nodes() == [crash]

    def test_isolated_node_is_both_root_and_leaf(self):
        graph = IncidentGraph()
        node = _make_node()
        graph.add_node(node)
        assert graph.get_root_nodes() == [node]
        assert graph.get_leaf_nodes() == [node]

    def test_incoming_edges_filtered_by_type(self):
        graph = IncidentGraph()
        a, b = _make_node(), _make_node()
        graph.add_node(a)
        graph.add_node(b)
        graph.add_edge(_make_edge(a, b, edge_type=EdgeType.CAUSAL, rule_id="r1"))
        graph.add_edge(_make_edge(a, b, edge_type=EdgeType.TEMPORAL, rule_id="r1"))
        causal_only = graph.incoming_edges(b.node_id, edge_type=EdgeType.CAUSAL)
        assert len(causal_only) == 1
        assert causal_only[0].edge_type == EdgeType.CAUSAL

    def test_outgoing_edges_filtered_by_type(self):
        graph = IncidentGraph()
        a, b = _make_node(), _make_node()
        graph.add_node(a)
        graph.add_node(b)
        graph.add_edge(_make_edge(a, b, edge_type=EdgeType.DEPENDENCY, rule_id="r1"))
        dep_only = graph.outgoing_edges(a.node_id, edge_type=EdgeType.DEPENDENCY)
        assert len(dep_only) == 1

    def test_incoming_edges_on_missing_node_raises(self):
        graph = IncidentGraph()
        with pytest.raises(NodeNotFoundError):
            graph.incoming_edges(uuid.uuid4())

    def test_outgoing_edges_on_missing_node_raises(self):
        graph = IncidentGraph()
        with pytest.raises(NodeNotFoundError):
            graph.outgoing_edges(uuid.uuid4())


class TestKHopNeighborhood:
    def _build_chain(self) -> tuple[IncidentGraph, list[GraphNode]]:
        """commit/deploy -> deploy_done -> crash -> health_check_fail -> lb"""
        graph = IncidentGraph()
        nodes = [
            _make_node(event_type=CoreEventType.DEPLOY_STARTED, component="ci"),
            _make_node(event_type=CoreEventType.DEPLOY_COMPLETED, component="ci"),
            _make_node(event_type=CoreEventType.PROCESS_CRASH, component="api-gateway"),
            _make_node(event_type=CoreEventType.HEALTH_CHECK_FAIL, component="api-gateway"),
            _make_node(event_type=CoreEventType.STATE_CHANGE, component="load-balancer"),
        ]
        for n in nodes:
            graph.add_node(n)
        for src, tgt in zip(nodes, nodes[1:], strict=False):
            graph.add_edge(_make_edge(src, tgt))
        return graph, nodes

    def test_zero_hops_returns_only_start_node(self):
        graph, nodes = self._build_chain()
        sub = graph.get_k_hop_neighborhood(nodes[2].node_id, max_hops=0)
        assert {n.node_id for n in sub.nodes} == {nodes[2].node_id}
        assert sub.edges == []

    def test_two_hops_expands_both_directions(self):
        graph, nodes = self._build_chain()
        sub = graph.get_k_hop_neighborhood(nodes[2].node_id, max_hops=2)
        expected = {n.node_id for n in nodes}  # whole 5-node chain is within 2 hops of the middle
        assert {n.node_id for n in sub.nodes} == expected

    def test_one_hop_does_not_reach_two_hops_away(self):
        graph, nodes = self._build_chain()
        sub = graph.get_k_hop_neighborhood(nodes[2].node_id, max_hops=1)
        expected = {nodes[1].node_id, nodes[2].node_id, nodes[3].node_id}
        assert {n.node_id for n in sub.nodes} == expected

    def test_missing_start_node_raises(self):
        graph = IncidentGraph()
        with pytest.raises(NodeNotFoundError):
            graph.get_k_hop_neighborhood(uuid.uuid4(), max_hops=2)

    def test_negative_max_hops_raises(self):
        graph, nodes = self._build_chain()
        with pytest.raises(ValueError):
            graph.get_k_hop_neighborhood(nodes[0].node_id, max_hops=-1)

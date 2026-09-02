import pytest
from unittest.mock import Mock
from uuid import uuid4, UUID
from datetime import datetime, timezone
from typing import Dict, List, Optional

from deployd.domain.causal.causal_engine import CausalEngine
from deployd.domain.graph.edge_type import EdgeType
from deployd.domain.graph.edge import GraphEdge
from deployd.domain.graph.node import GraphNode
from deployd.domain.graph.graph import IncidentGraph


class TestCausalEngine:

    @pytest.fixture
    def graph_and_ids(self) -> tuple[IncidentGraph, Dict[str, UUID]]:
        """
        Builds a sample graph and returns the mock graph along with a dictionary
        mapping symbolic names to UUIDs.
        """
        nodes_by_name = {}
        ids_by_name = {}
        timestamps = {
            "A": datetime(2023, 1, 1, 10, 0, 0, tzinfo=timezone.utc),
            "B": datetime(2023, 1, 1, 10, 0, 20, tzinfo=timezone.utc),
            "C": datetime(2023, 1, 1, 10, 0, 15, tzinfo=timezone.utc),
            "D": datetime(2023, 1, 1, 10, 0, 30, tzinfo=timezone.utc),
            "E": datetime(2023, 1, 1, 10, 0, 25, tzinfo=timezone.utc),
            "F": datetime(2023, 1, 1, 10, 0, 35, tzinfo=timezone.utc),
            "G": datetime(2023, 1, 1, 10, 0, 40, tzinfo=timezone.utc),
            "X": datetime(2023, 1, 1, 10, 0, 5, tzinfo=timezone.utc),
            "Y": datetime(2023, 1, 1, 10, 0, 10, tzinfo=timezone.utc),
        }
        for name, ts in timestamps.items():
            node_id = uuid4()
            event = Mock()
            event.timestamp = ts
            node = Mock(spec=GraphNode)
            node.id = node_id
            node.event = event
            nodes_by_name[name] = node
            ids_by_name[name] = node_id

        edges = [
            GraphEdge(source=ids_by_name["A"], target=ids_by_name["B"], edge_type=EdgeType.CAUSAL, confidence=1.0),
            GraphEdge(source=ids_by_name["B"], target=ids_by_name["C"], edge_type=EdgeType.CAUSAL, confidence=1.0),
            GraphEdge(source=ids_by_name["C"], target=ids_by_name["D"], edge_type=EdgeType.CAUSAL, confidence=1.0),
            GraphEdge(source=ids_by_name["E"], target=ids_by_name["F"], edge_type=EdgeType.DEPENDENCY, confidence=1.0),
            GraphEdge(source=ids_by_name["F"], target=ids_by_name["G"], edge_type=EdgeType.DEPENDENCY, confidence=1.0),
            GraphEdge(source=ids_by_name["D"], target=ids_by_name["E"], edge_type=EdgeType.DEPENDENCY, confidence=1.0),
            GraphEdge(source=ids_by_name["X"], target=ids_by_name["Y"], edge_type=EdgeType.OBSERVED_AS, confidence=1.0),
        ]

        graph_mock = Mock(spec=IncidentGraph)

        def get_node(node_id: UUID) -> GraphNode:
            for node in nodes_by_name.values():
                if node.id == node_id:
                    return node
            raise KeyError(f"Node {node_id} not found")
        graph_mock.get_node.side_effect = get_node

        def outgoing_edges(node_id: UUID, edge_type: Optional[EdgeType] = None) -> List[GraphEdge]:
            result = []
            for edge in edges:
                if edge.source == node_id and (edge_type is None or edge.edge_type == edge_type):
                    result.append(edge)
            return result
        graph_mock.outgoing_edges.side_effect = outgoing_edges

        def incoming_edges(node_id: UUID, edge_type: Optional[EdgeType] = None) -> List[GraphEdge]:
            result = []
            for edge in edges:
                if edge.target == node_id and (edge_type is None or edge.edge_type == edge_type):
                    result.append(edge)
            return result
        graph_mock.incoming_edges.side_effect = incoming_edges

        def get_k_hop_neighborhood(start_id: UUID, max_hops: int):
            visited = set()
            frontier = [(start_id, 0)]
            while frontier:
                curr_id, depth = frontier.pop(0)
                if curr_id in visited:
                    continue
                visited.add(curr_id)
                if depth < max_hops:
                    for edge in edges:
                        if edge.source == curr_id and edge.target not in visited:
                            frontier.append((edge.target, depth+1))
                        if edge.target == curr_id and edge.source not in visited:
                            frontier.append((edge.source, depth+1))
            class Subgraph:
                pass
            sub = Subgraph()
            sub.nodes = [node for name, node in nodes_by_name.items() if node.id in visited]
            return sub
        graph_mock.get_k_hop_neighborhood.side_effect = get_k_hop_neighborhood

        def get_filtered_k_hop(start_id: UUID, max_hops: int, edge_types: Optional[List[EdgeType]] = None):
            visited = set()
            frontier = [(start_id, 0)]
            allowed_types = set(edge_types) if edge_types else set()
            while frontier:
                curr_id, depth = frontier.pop(0)
                if curr_id in visited:
                    continue
                visited.add(curr_id)
                if depth < max_hops:
                    for edge in edges:
                        if edge_types is None or edge.edge_type in allowed_types:
                            if edge.source == curr_id and edge.target not in visited:
                                frontier.append((edge.target, depth+1))
                            if edge.target == curr_id and edge.source not in visited:
                                frontier.append((edge.source, depth+1))
            class Subgraph:
                pass
            sub = Subgraph()
            sub.nodes = [node for name, node in nodes_by_name.items() if node.id in visited]
            return sub
        graph_mock.get_filtered_k_hop.side_effect = get_filtered_k_hop

        return graph_mock, ids_by_name

    def test_causal_chain_forward(self, graph_and_ids):
        """Tests that the causal chain returns correctly ordered nodes starting from various points."""
        graph, ids = graph_and_ids
        engine = CausalEngine(graph)

        chain = engine.causal_chain(ids["A"])
        assert len(chain) == 4
        assert chain[0].id == ids["A"]
        assert chain[1].id == ids["B"]
        assert chain[2].id == ids["C"]
        assert chain[3].id == ids["D"]

        chain = engine.causal_chain(ids["B"])
        assert len(chain) == 3
        assert chain[0].id == ids["B"]
        assert chain[1].id == ids["C"]
        assert chain[2].id == ids["D"]

        chain = engine.causal_chain(ids["D"])
        assert len(chain) == 1
        assert chain[0].id == ids["D"]

        chain = engine.causal_chain(ids["E"])
        assert len(chain) == 1
        assert chain[0].id == ids["E"]

    def test_causal_chain_stops_at_boundary(self, graph_and_ids):
        """Tests that the causal chain stops when no causal outgoing edges exist."""
        graph, ids = graph_and_ids
        engine = CausalEngine(graph)
        
        chain = engine.causal_chain(ids["D"])
        assert len(chain) == 1

        chain = engine.causal_chain(ids["A"])
        assert chain[-1].id == ids["D"]

    def test_temporal_order_sorts_by_timestamp(self, graph_and_ids):
        """Tests that the temporal order correctly sorts nodes by timestamp."""
        graph, ids = graph_and_ids
        engine = CausalEngine(graph)

        ordered = engine.temporal_order(ids["A"], max_hops=2)
        assert len(ordered) == 3
        assert ordered[0].id == ids["A"]
        assert ordered[1].id == ids["C"]
        assert ordered[2].id == ids["B"]

        # From E with max_hops=2 (bidirectional): E, D (hop1), then F, G, C (hop2 via D←C)
        ordered = engine.temporal_order(ids["E"], max_hops=2)
        assert len(ordered) == 5
        expected_order = [ids["C"], ids["E"], ids["D"], ids["F"], ids["G"]]
        assert [node.id for node in ordered] == expected_order

    def test_dependency_expansion_upstream_what_this_depends_on(self, graph_and_ids):
        """Tests dependency expansion for upstream dependencies (what this depends on).
        Traverses outgoing DEPENDENCY edges (e.g. D depends on E)."""
        graph, ids = graph_and_ids
        engine = CausalEngine(graph)

        # From D, it depends on E, which depends on F
        nodes = engine.dependency_expansion(ids["D"], max_hops=2)
        assert len(nodes) == 3
        assert set([n.id for n in nodes]) == {ids["D"], ids["E"], ids["F"]}

    def test_dependency_expansion_downstream_what_depends_on_this(self, graph_and_ids):
        """Tests dependency expansion for downstream dependencies (what depends on this).
        Traverses incoming DEPENDENCY edges (e.g. D depends on E, so from E we find D)."""
        graph, ids = graph_and_ids
        engine = CausalEngine(graph)

        # From E, it depends on F and G (outgoing), and D depends on E (incoming)
        nodes = engine.dependency_expansion(ids["E"], max_hops=2)
        assert len(nodes) == 4
        assert set([n.id for n in nodes]) == {ids["D"], ids["E"], ids["F"], ids["G"]}

    def test_dependency_expansion_respects_max_hops(self, graph_and_ids):
        """Tests that dependency expansion does not exceed max_hops."""
        graph, ids = graph_and_ids
        engine = CausalEngine(graph)

        nodes = engine.dependency_expansion(ids["D"], max_hops=1)
        assert len(nodes) == 2
        assert set([n.id for n in nodes]) == {ids["D"], ids["E"]}

        nodes = engine.dependency_expansion(ids["D"], max_hops=3)
        assert len(nodes) == 4
        assert set([n.id for n in nodes]) == {ids["D"], ids["E"], ids["F"], ids["G"]}

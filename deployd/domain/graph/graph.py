"""Graph structure. `ADR-003` `ADR-004` Related to this file"""

from __future__ import annotations

import uuid  # noqa: TCH003
from collections import deque

from deployd.domain.graph.edge import GraphEdge  # noqa: TCH001
from deployd.domain.graph.edge_type import EdgeType  # noqa: TCH001
from deployd.domain.graph.node import GraphNode  # noqa: TCH001


class GraphError(Exception):
    """Base Class for all IncidentGraph domain errors"""


class DuplicateNodeError(GraphError):
    """A node with same node_id already exists in the graph"""


class NodeNotFoundError(GraphError):
    """A Operation referenced node_id that dose not exist in graph"""


class SelfLoopError(GraphError):
    """Attempt to add self loop edge"""


class DuplicateEdgeError(GraphError):
    """Attempt to add duplicate edge"""


class IncidentGraph:
    """
    Directed Graph of GraphNodes and GraphEdges
    An investigation builds this incrementally as evicende is collected.
    """

    def __init__(self) -> None:
        self._nodes: dict[uuid.UUID, GraphNode] = {}
        self._outgoing: dict[uuid.UUID, list[GraphEdge]] = {}
        self._incoming: dict[uuid.UUID, list[GraphEdge]] = {}

    def add_node(self, node: GraphNode) -> None:
        if node.node_id in self._nodes:
            raise DuplicateNodeError(f"Node {node.node_id} already exists in the graph")
        self._nodes[node.node_id] = node
        self._outgoing[node.node_id] = []
        self._incoming[node.node_id] = []

    def add_edge(self, edge: GraphEdge) -> None:
        if edge.source == edge.target:
            raise SelfLoopError("Attempted to add self loop edge")
        if edge.source not in self._nodes:
            raise NodeNotFoundError(f"Edge source {edge.source} is not in the graph")
        if edge.target not in self._nodes:
            raise NodeNotFoundError(f"Edge target {edge.target} is not in the graph")
        for existing in self._outgoing[edge.source]:
            if (
                existing.target == edge.target
                and existing.edge_type == edge.edge_type
                and existing.rule_id == edge.rule_id
            ):
                raise DuplicateEdgeError(
                    f"Duplicate edge {edge.edge_type} between nodes {edge.source} and {edge.target}"
                )
        self._outgoing[edge.source].append(edge)
        self._incoming[edge.target].append(edge)

    def get_node(self, node_id: uuid.UUID) -> GraphNode:
        try:
            return self._nodes[node_id]
        except KeyError as err:
            raise NodeNotFoundError(f"Node {node_id} not found in the graph") from err

    @property
    def nodes(self) -> list[GraphNode]:
        return list(self._nodes.values())

    @property
    def edges(self) -> list[GraphEdge]:
        all_edges: list[GraphEdge] = []
        for edge_list in self._outgoing.values():
            all_edges.extend(edge_list)
        return all_edges

    def get_root_nodes(self) -> list[GraphNode]:
        return [
            self._nodes[node_id] for node_id, incoming in self._incoming.items() if not incoming
        ]

    def get_leaf_nodes(self) -> list[GraphNode]:
        return [
            self._nodes[node_id] for node_id, outgoing in self._outgoing.items() if not outgoing
        ]

    def incoming_edges(
        self, node_id: uuid.UUID, edge_type: EdgeType | None = None
    ) -> list[GraphEdge]:
        if node_id not in self._nodes:
            raise NodeNotFoundError(f"Node {node_id} not found in the graph")
        edges = self._incoming[node_id]
        if edge_type is None:
            return list(edges)
        return [e for e in edges if e.edge_type == edge_type]

    def outgoing_edges(
        self, node_id: uuid.UUID, edge_type: EdgeType | None = None
    ) -> list[GraphEdge]:
        if node_id not in self._nodes:
            raise NodeNotFoundError(f"Node {node_id} not found in the graph")
        edges = self._outgoing[node_id]
        if edge_type is None:
            return list(edges)
        return [e for e in edges if e.edge_type == edge_type]

    # Neighborhood expansion algo
    def get_k_hop_neighborhood(self, start_node_id: uuid.UUID, max_hops: int = 2) -> IncidentGraph:
        """ADR-004;
        Constrained BFS from start up to max hops , bidirectioanl.
        Returns a new IncidentGraph containing the sub-graph reached within max hops.
        Raises NodeNotFoundError if start_node_id isn't in the graph.
        """
        if start_node_id not in self._nodes:
            raise NodeNotFoundError(f"start node {start_node_id} not found")
        if max_hops <= 0:
            raise ValueError("max_hops must be positive")

        visited: set[uuid.UUID] = {start_node_id}
        frontier: deque[tuple[uuid.UUID, int]] = deque([(start_node_id, 0)])
        while frontier:
            curr_id, depth = frontier.popleft()
            if depth == max_hops:
                continue
            neighbor_ids = [e.target for e in self._outgoing[curr_id]] + [
                e.source for e in self._incoming[curr_id]
            ]
            for neighbor_id in neighbor_ids:
                if neighbor_id not in visited:
                    visited.add(neighbor_id)
                    frontier.append((neighbor_id, depth + 1))

        subgraph = IncidentGraph()
        for node_id in visited:
            subgraph.add_node(self.get_node(node_id))
        for edge in self.edges:
            if edge.source in visited and edge.target in visited:
                subgraph.add_edge(edge)
        return subgraph

    # def edge_time_delta

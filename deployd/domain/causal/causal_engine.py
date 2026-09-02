from uuid import UUID

from deployd.domain.graph.edge_type import EdgeType
from deployd.domain.graph.graph import IncidentGraph
from deployd.domain.graph.node import GraphNode


class CausalEngine:
    def __init__(self, graph: IncidentGraph) -> None:
        self.graph = graph

    def causal_chain(self, start_node_id: UUID) -> list[list[GraphNode]]:
        paths: list[list[GraphNode]] = []

        def dfs(current_id: UUID, current_path: list[GraphNode], visited: set[UUID]) -> None:
            try:
                node = self.graph.get_node(current_id)
            except Exception:
                # Let the caller handle the error; re-raise with context if needed
                raise

            if current_id in visited:
                paths.append(current_path + [node])
                return

            new_visited = visited | {current_id}
            path_with_node = current_path + [node]

            # Get only outgoing CAUSAL edges from the current node.
            outgoing_causal = self.graph.outgoing_edges(current_id, EdgeType.CAUSAL)

            if not outgoing_causal:
                # No further causes → end of chain.
                paths.append(path_with_node)
                return

            for edge in outgoing_causal:
                dfs(edge.target, path_with_node, new_visited)

        dfs(start_node_id, [], set())
        return paths

    def temporal_order(self, start_node_id: UUID, max_hops: int = 2) -> list[GraphNode]:
        # Delegate the entire graph expansion to IncidentGraph.
        subgraph = self.graph.get_k_hop_neighborhood(start_node_id, max_hops)

        # Extract nodes from the subgraph and sort by timestamp.
        nodes = subgraph.nodes
        sorted_nodes = sorted(nodes, key=lambda n: n.event.timestamp)
        return sorted_nodes

    def dependency_expansion(self, start_node_id: UUID, max_hops: int = 2) -> list[GraphNode]:
        subgraph = self.graph.get_k_hop_neighborhood(
            start_node_id, max_hops, edge_types=[EdgeType.DEPENDENCY]
        )
        return subgraph.nodes

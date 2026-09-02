from uuid import UUID
from typing import List,Optional

from deployd.domain.graph.graph import IncidentGraph
from deployd.domain.graph.edge_type import EdgeType
from deployd.domain.graph.edge import GraphEdge
from deployd.domain.graph.node import GraphNode


class CausalEngine:

    def __iniy__(self,graph:IncidentGraph):
        self.graph= graph
    
    def causal_chain (self, start_node_id: UUID) -> List[GraphNode]:
        chain: List[GraphNode]=[]
        current_id= start_node_id

        while True:
            try:
                node=self.graph.get_node(current_id)
                chain.append(node)

            except Exception:
                # Let the caller handle the error; re-raise with context if needed
                raise

            # Get only outgoing CAUSAL edges from the current node.
            outgoing_causal= self.graph.outgoing_edges(current_id, EdgeType.CAUSAL)

            if not outgoing_causal:
                # No further causes → end of chain.
                break

            next_edge= outgoing_causal[0]
            current_id= next_edge.target
        
        return chain
                
    def temporal_order(self,start_node_id: UUID , max_hops: int =2) -> List[GraphNode]:

        # Delegate the entire graph expansion to IncidentGraph.
        subgraph= self.graph.get_k_hop_neighborhood(start_node_id,max_hops)

        # Extract nodes from the subgraph and sort by timestamp.
        nodes = subgraph.nodes
        sorted_nodes=sorted(nodes, key=lambda n : n.event.timestamp)
        return sorted_nodes
        
    def dependency_expansion(self, start_node_id: UUID, max_hops: int = 2) -> List[GraphNode]:
        subgraph = self.graph.get_filtered_k_hop(
            start_node_id, max_hops, [EdgeType.DEPENDENCY]
        )
        return subgraph.nodes

        
    
"""comparision between curr incident causal chain and each historical runbook"""

from __future__ import annotations

from dataclasses import dataclass

from deployd.adapters.outgoing.vector_store.graph_store import GraphStore  # noqa: TCH002


@dataclass(frozen=True)
class StructuralHit:
    runbook_id: str
    causal_score: float
    component_score: float


class GraphIndex:
    def __init__(self, graph_store: GraphStore) -> None:
        self._graph_store = graph_store

    def search(
        self, current_causal_chain: tuple[str, ...], current_components: frozenset[str], top_k: int
    ) -> list[StructuralHit]:
        hits = [
            StructuralHit(
                runbook_id=structure.runbook_id,
                causal_score=self._causal_chain_similarity(  # type: ignore[attr-defined]
                    current_causal_chain, structure.causal_chain
                ),
                component_score=self._component_overlap(  # type: ignore[attr-defined]
                    current_components, structure.affected_components
                ),
            )
            for structure in self._graph_store.all()
        ]
        hits.sort(key=lambda h: h.causal_score, reverse=True)
        return hits[:top_k]


# fuck this

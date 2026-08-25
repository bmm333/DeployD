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
                causal_score=self._causal_chain_similarity(
                    current_causal_chain, structure.causal_chain
                ),
                component_score=self._component_overlap(
                    current_components, structure.affected_components
                ),
            )
            for structure in self._graph_store.all()
        ]
        hits.sort(key=lambda h: h.causal_score, reverse=True)
        return hits[:top_k]

    @staticmethod
    def _causal_chain_similarity(chain_a: tuple[str, ...], chain_b: tuple[str, ...]) -> float:
        """LSC ratio, length of the longer chains. same event on the same order is strong signal"""
        if not chain_a or not chain_b:
            return 0.0
        lcs_length = _longest_common_subsequence_length(chain_a, chain_b)
        return lcs_length / max(len(chain_a), len(chain_b))

    @staticmethod
    def _component_overlap(components_a: frozenset[str], components_b: frozenset[str]) -> float:
        """Jaccard"""
        union = components_a | components_b
        if not union:
            return 0.0
        return len(components_a & components_b) / len(union)


def _longest_common_subsequence_length(a: tuple[str, ...], b: tuple[str, ...]) -> int:
    dp = [[0] * (len(b) + 1) for _ in range(len(a) + 1)]
    for i in range(1, len(a) + 1):
        for j in range(1, len(b) + 1):
            if a[i - 1] == b[j - 1]:
                dp[i][j] = dp[i - 1][j - 1] + 1
            else:
                dp[i][j] = max(dp[i - 1][j], dp[i][j - 1])
    return dp[len(a)][len(b)]

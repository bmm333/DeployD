"""blending dense and spares and struct score into a single score per runnbook"""

from __future__ import annotations

from dataclasses import dataclass, replace

from deployd.adapters.outgoing.vector_store.bm25_index import SparseHit  # noqa: TCH002
from deployd.adapters.outgoing.vector_store.dense_index import DenseHit  # noqa: TCH002
from deployd.adapters.outgoing.vector_store.graph_index import StructuralHit  # noqa: TCH002

# reasoing behind the weights (For now ):
# semanitc 0.35 catches description of the same prob, but less reliable signal alone for thi context
# bm25 0.2 catches exact identifiers but lower weight than sematic cuz is narrower and fails on paraphrasing.
# causal 0.3 weighted close to semantic bcs an identical causal chain in the same orer is a very strong signa.
# this is the point of constructing a graph native RAG instead of a generic one.
# component 0.15 a correlation signal, not strong on its own. two indipendent bug can hit the same service.
# sum 1.0 . But , but this is pre running the system so we will definitly return to this file and make adjustments
# i cannot put my hand on fire for this setup. this is just a start.

SEMANTIC_WEIGHT = 0.35
BM25_WEIGHT = 0.20
CAUSAL_WEIGHT = 0.30
COMPONENT_WEIGHT = 0.15


@dataclass(frozen=True)
class ScoreSet:
    semantic: float = 0.0
    bm25: float = 0.0
    causal: float = 0.0
    component: float = 0.0


def blend(
    dense_hits: list[DenseHit],
    spares_hits: list[SparseHit],
    structural_hits: list[StructuralHit],
) -> list[tuple[str, ScoreSet, float]]:
    """Merge three indpt score list by runbook id and calculate final score,
    Return runbook id , scoreset and final score, sorted desceding final score"""
    scores: dict[str, ScoreSet] = {}

    for hit in dense_hits:
        scores[hit.runbook_id] = replace(
            scores.get(hit.runbook_id, ScoreSet()), semantic=hit.semantic_score
        )
    for hit in spares_hits:
        scores[hit.runbook_id] = replace(
            scores.get(hit.runbook_id, ScoreSet()), bm25=hit.bm25_score
        )
    for hit in structural_hits:
        scores[hit.runbook_id] = replace(
            scores.get(hit.runbook_id, ScoreSet()),
            causal=hit.causal_score,
            component=hit.component_score,
        )
    results = []
    for runbook_id, score_set in scores.items():
        final_score = (
            SEMANTIC_WEIGHT * score_set.semantic
            + BM25_WEIGHT * score_set.bm25
            + CAUSAL_WEIGHT * score_set.causal
            + COMPONENT_WEIGHT * score_set.component
        )
        results.append((runbook_id, score_set, final_score))
    results.sort(key=lambda r: r[2], reverse=True)
    return results

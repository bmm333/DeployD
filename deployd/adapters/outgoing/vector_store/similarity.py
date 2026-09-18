"""blending dense and spares and struct score into a single score per runnbook"""

from __future__ import annotations

from dataclasses import dataclass, replace

from deployd.adapters.outgoing.vector_store.bm25_index import SparseHit  # noqa: TCH002
from deployd.adapters.outgoing.vector_store.chroma_client import DenseHit  # noqa: TCH002
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


def blend_linear(
    dense_hits: list[DenseHit],
    spares_hits: list[SparseHit],
    structural_hits: list[StructuralHit],
) -> list[tuple[str, ScoreSet, float]]:
    """Merge three indpt score list by runbook id and calculate final score,
    Return runbook id , scoreset and final score, sorted desceding final score"""
    scores: dict[str, ScoreSet] = {}

    for dense_hit in dense_hits:
        scores[dense_hit.runbook_id] = replace(
            scores.get(dense_hit.runbook_id, ScoreSet()), semantic=dense_hit.semantic_score
        )
    for sparse_hit in spares_hits:
        scores[sparse_hit.runbook_id] = replace(
            scores.get(sparse_hit.runbook_id, ScoreSet()), bm25=sparse_hit.bm25_score
        )
    for struct_hit in structural_hits:
        scores[struct_hit.runbook_id] = replace(
            scores.get(struct_hit.runbook_id, ScoreSet()),
            causal=struct_hit.causal_score,
            component=struct_hit.component_score,
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


# Backward compatibility alias
blend = blend_linear


def blend_rrf(
    dense_hits: list[DenseHit],
    spares_hits: list[SparseHit],
    structural_hits: list[StructuralHit],
    k: int = 60
) -> list[tuple[str, ScoreSet, float]]:
    """Merge scores using Reciprocal Rank Fusion.
    Return runbook id, scoreset (holding original scores for debug), and final RRF score, sorted descending."""
    scores: dict[str, ScoreSet] = {}
    rrf_scores: dict[str, float] = {}

    # Sort each incoming list by its respective score descending to determine rank
    dense_hits = sorted(dense_hits, key=lambda x: x.semantic_score, reverse=True)
    spares_hits = sorted(spares_hits, key=lambda x: x.bm25_score, reverse=True)
    structural_hits_causal = sorted(structural_hits, key=lambda x: x.causal_score, reverse=True)
    structural_hits_component = sorted(structural_hits, key=lambda x: x.component_score, reverse=True)

    # Process Semantic (Dense)
    for rank, hit in enumerate(dense_hits, start=1):
        if hit.semantic_score > 0:
            scores[hit.runbook_id] = replace(
                scores.get(hit.runbook_id, ScoreSet()), semantic=hit.semantic_score
            )
            rrf_scores[hit.runbook_id] = rrf_scores.get(hit.runbook_id, 0.0) + 1.0 / (k + rank)
        else:
            scores[hit.runbook_id] = replace(
                scores.get(hit.runbook_id, ScoreSet()), semantic=0.0
            )

    # Process BM25 (Sparse)
    for rank, hit in enumerate(spares_hits, start=1):
        if hit.bm25_score > 0:
            scores[hit.runbook_id] = replace(
                scores.get(hit.runbook_id, ScoreSet()), bm25=hit.bm25_score
            )
            rrf_scores[hit.runbook_id] = rrf_scores.get(hit.runbook_id, 0.0) + 1.0 / (k + rank)
        else:
            scores[hit.runbook_id] = replace(
                scores.get(hit.runbook_id, ScoreSet()), bm25=0.0
            )

    # Process Causal and Component
    # Causal
    for rank, hit in enumerate(structural_hits_causal, start=1):
        if hit.causal_score > 0:
            scores[hit.runbook_id] = replace(
                scores.get(hit.runbook_id, ScoreSet()), causal=hit.causal_score
            )
            rrf_scores[hit.runbook_id] = rrf_scores.get(hit.runbook_id, 0.0) + 1.0 / (k + rank)
        else:
            scores[hit.runbook_id] = replace(
                scores.get(hit.runbook_id, ScoreSet()), causal=0.0
            )

    # Component
    for rank, hit in enumerate(structural_hits_component, start=1):
        if hit.component_score > 0:
            scores[hit.runbook_id] = replace(
                scores.get(hit.runbook_id, ScoreSet()), component=hit.component_score
            )
            rrf_scores[hit.runbook_id] = rrf_scores.get(hit.runbook_id, 0.0) + 1.0 / (k + rank)
        else:
            scores[hit.runbook_id] = replace(
                scores.get(hit.runbook_id, ScoreSet()), component=0.0
            )

    results = []
    for runbook_id, score_set in scores.items():
        results.append((runbook_id, score_set, rrf_scores.get(runbook_id, 0.0)))
    
    results.sort(key=lambda r: r[2], reverse=True)
    return results

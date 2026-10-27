"""Unit tests for HybridRetriever: signal fusion, structural context, gate threshold."""

from __future__ import annotations

from typing import Any

import pytest
from deployd.adapters.outgoing.vector_store.bm25_index import SparseHit
from deployd.adapters.outgoing.vector_store.chroma_client import DenseHit
from deployd.adapters.outgoing.vector_store.graph_index import GraphIndex
from deployd.adapters.outgoing.vector_store.graph_store import GraphStore, RunbookStructure
from deployd.adapters.outgoing.vector_store.hybrid_retriever import HybridRetriever
from deployd.adapters.outgoing.vector_store.similarity import (
    BM25_WEIGHT,
    CAUSAL_WEIGHT,
    COMPONENT_WEIGHT,
    SEMANTIC_WEIGHT,
)


class _FakeChroma:
    def __init__(self, hits: list[DenseHit]) -> None:
        self._hits = hits

    def search(self, query: str, top_k: int, **_: Any) -> list[DenseHit]:
        return self._hits[:top_k]


class _FakeBM25:
    def __init__(self, hits: list[SparseHit]) -> None:
        self._hits = hits

    def search(self, query: str, top_k: int) -> list[SparseHit]:
        return self._hits[:top_k]


def _graph_index() -> GraphIndex:
    store = GraphStore()
    store.add(
        RunbookStructure(
            runbook_id="RB-OOM",
            causal_chain=("DEPLOY_STARTED", "RESOURCE_EXHAUSTION", "PROCESS_CRASH"),
            affected_components=frozenset({"auth-service"}),
        )
    )
    store.add(
        RunbookStructure(
            runbook_id="RB-DB",
            causal_chain=("DEPENDENCY_FAILURE",),
            affected_components=frozenset({"payment-service"}),
        )
    )
    return GraphIndex(store)


def _retriever(graph_index: GraphIndex | None = None) -> HybridRetriever:
    chroma = _FakeChroma([DenseHit("RB-OOM", 0.8), DenseHit("RB-DB", 0.4)])
    bm25 = _FakeBM25([SparseHit("RB-OOM", 1.0), SparseHit("RB-DB", 0.2)])
    return HybridRetriever(
        chroma=chroma,  # type: ignore[arg-type]  # structural fake, no Chroma needed
        bm25=bm25,  # type: ignore[arg-type]  # structural fake, no index needed
        confidence_threshold=0.5,
        graph_index=graph_index,
    )


OOM_CHAIN = ("DEPLOY_STARTED", "RESOURCE_EXHAUSTION", "PROCESS_CRASH")


def test_without_structural_context_only_dense_and_sparse_are_fused() -> None:
    result, breakdown = _retriever(_graph_index()).retrieve_scored("auth oom")

    top = result.candidates[0]
    assert top.runbook_id == "RB-OOM"
    assert top.score == pytest.approx(SEMANTIC_WEIGHT * 0.8 + BM25_WEIGHT * 1.0)
    assert breakdown["RB-OOM"].causal == 0.0
    assert breakdown["RB-OOM"].component == 0.0
    assert not result.has_strong_match  # 0.48 < 0.5: text alone cannot open the gate


def test_structural_context_adds_causal_and_component_signals() -> None:
    result, breakdown = _retriever(_graph_index()).retrieve_scored(
        "auth oom", causal_chain=OOM_CHAIN, components=frozenset({"auth-service"})
    )

    top = result.candidates[0]
    assert top.runbook_id == "RB-OOM"
    assert breakdown["RB-OOM"].causal == pytest.approx(1.0)
    assert breakdown["RB-OOM"].component == pytest.approx(1.0)
    assert top.score == pytest.approx(
        SEMANTIC_WEIGHT * 0.8 + BM25_WEIGHT * 1.0 + CAUSAL_WEIGHT + COMPONENT_WEIGHT
    )
    assert result.has_strong_match
    assert [c.runbook_id for c in result.strong_candidates] == ["RB-OOM"]


def test_structural_context_is_ignored_without_graph_index() -> None:
    result, breakdown = _retriever(None).retrieve_scored(
        "auth oom", causal_chain=OOM_CHAIN, components=frozenset({"auth-service"})
    )

    assert breakdown["RB-OOM"].causal == 0.0
    assert not result.has_strong_match


def test_retrieve_matches_retrieve_scored_and_exposes_threshold() -> None:
    retriever = _retriever(_graph_index())
    plain = retriever.retrieve("auth oom", causal_chain=OOM_CHAIN)
    scored, _ = retriever.retrieve_scored("auth oom", causal_chain=OOM_CHAIN)

    assert plain == scored
    assert retriever.confidence_threshold == 0.5
    assert plain.confidence_threshold == 0.5


def test_top_k_limits_candidates_and_breakdown() -> None:
    result, breakdown = _retriever(_graph_index()).retrieve_scored("auth oom", top_k=1)

    assert len(result.candidates) == 1
    assert set(breakdown) == {"RB-OOM"}


def test_scores_are_plain_python_floats() -> None:
    import numpy as np

    bm25 = _FakeBM25([SparseHit("RB-OOM", np.float64(1.0))])  # type: ignore[arg-type]  # BM25Okapi returns numpy
    retriever = HybridRetriever(
        chroma=_FakeChroma([DenseHit("RB-OOM", 0.8)]),  # type: ignore[arg-type]  # structural fake
        bm25=bm25,  # type: ignore[arg-type]  # structural fake
        graph_index=_graph_index(),
    )
    result, breakdown = retriever.retrieve_scored("auth oom", causal_chain=OOM_CHAIN)

    # np.float64 subclasses float, so isinstance(..., float) alone would not catch it
    assert not isinstance(result.candidates[0].score, np.floating)
    assert not isinstance(breakdown["RB-OOM"].bm25, np.floating)

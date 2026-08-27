"""Unit tests for the blend function - fusion of dense, sparse, and structural scores"""

import pytest
from deployd.adapters.outgoing.vector_store.bm25_index import SparseHit
from deployd.adapters.outgoing.vector_store.chroma_client import DenseHit
from deployd.adapters.outgoing.vector_store.graph_index import StructuralHit
from deployd.adapters.outgoing.vector_store.similarity import (
    BM25_WEIGHT,
    CAUSAL_WEIGHT,
    COMPONENT_WEIGHT,
    SEMANTIC_WEIGHT,
    ScoreSet,
    blend,
)


class TestScoreSet:
    def test_defaults_to_zero(self) -> None:
        ss = ScoreSet()
        assert ss.semantic == 0.0
        assert ss.bm25 == 0.0
        assert ss.causal == 0.0
        assert ss.component == 0.0

    def test_frozen(self) -> None:
        ss = ScoreSet(semantic=0.5)
        with pytest.raises(AttributeError):
            ss.semantic = 0.9  # type: ignore[misc]


class TestWeights:
    def test_weights_sum_to_one(self) -> None:
        total = SEMANTIC_WEIGHT + BM25_WEIGHT + CAUSAL_WEIGHT + COMPONENT_WEIGHT
        assert total == pytest.approx(1.0)


class TestBlend:
    def test_single_source_only_dense(self) -> None:
        results = blend(
            dense_hits=[DenseHit(runbook_id="rb-1", semantic_score=0.8)],
            spares_hits=[],
            structural_hits=[],
        )
        assert len(results) == 1
        rb_id, score_set, final = results[0]
        assert rb_id == "rb-1"
        assert score_set.semantic == 0.8
        assert score_set.bm25 == 0.0
        assert final == pytest.approx(SEMANTIC_WEIGHT * 0.8)

    def test_single_source_only_sparse(self) -> None:
        results = blend(
            dense_hits=[],
            spares_hits=[SparseHit(runbook_id="rb-1", bm25_score=1.0)],
            structural_hits=[],
        )
        _, score_set, final = results[0]
        assert score_set.bm25 == 1.0
        assert final == pytest.approx(BM25_WEIGHT * 1.0)

    def test_single_source_only_structural(self) -> None:
        results = blend(
            dense_hits=[],
            spares_hits=[],
            structural_hits=[
                StructuralHit(runbook_id="rb-1", causal_score=0.6, component_score=0.4)
            ],
        )
        _, score_set, final = results[0]
        assert score_set.causal == 0.6
        assert score_set.component == 0.4
        assert final == pytest.approx(CAUSAL_WEIGHT * 0.6 + COMPONENT_WEIGHT * 0.4)

    def test_all_three_sources_merged(self) -> None:
        """Same runbook_id from all three sources should merge into one entry."""
        results = blend(
            dense_hits=[DenseHit("rb-1", semantic_score=0.9)],
            spares_hits=[SparseHit("rb-1", bm25_score=0.7)],
            structural_hits=[StructuralHit("rb-1", causal_score=0.8, component_score=0.5)],
        )
        assert len(results) == 1
        _, score_set, final = results[0]
        expected = (
            SEMANTIC_WEIGHT * 0.9 + BM25_WEIGHT * 0.7 + CAUSAL_WEIGHT * 0.8 + COMPONENT_WEIGHT * 0.5
        )
        assert final == pytest.approx(expected)

    def test_different_runbooks_produce_separate_entries(self) -> None:
        results = blend(
            dense_hits=[DenseHit("rb-1", 0.9), DenseHit("rb-2", 0.3)],
            spares_hits=[],
            structural_hits=[],
        )
        assert len(results) == 2
        ids = {r[0] for r in results}
        assert ids == {"rb-1", "rb-2"}

    def test_sorted_by_final_score_descending(self) -> None:
        results = blend(
            dense_hits=[DenseHit("rb-low", 0.1), DenseHit("rb-high", 0.9)],
            spares_hits=[SparseHit("rb-low", 0.1), SparseHit("rb-high", 0.9)],
            structural_hits=[],
        )
        assert results[0][0] == "rb-high"
        assert results[1][0] == "rb-low"
        assert results[0][2] >= results[1][2]

    def test_all_empty_returns_empty(self) -> None:
        results = blend([], [], [])
        assert results == []

    def test_perfect_scores_produce_one(self) -> None:
        """If every signal is 1.0, weighted sum should be 1.0."""
        results = blend(
            dense_hits=[DenseHit("rb-1", 1.0)],
            spares_hits=[SparseHit("rb-1", 1.0)],
            structural_hits=[StructuralHit("rb-1", 1.0, 1.0)],
        )
        _, _, final = results[0]
        assert final == pytest.approx(1.0)

    def test_partial_overlap_across_sources(self) -> None:
        """rb-1 in dense + structural, rb-2 only in sparse — should produce 2 entries."""
        results = blend(
            dense_hits=[DenseHit("rb-1", 0.8)],
            spares_hits=[SparseHit("rb-2", 1.0)],
            structural_hits=[StructuralHit("rb-1", 0.6, 0.3)],
        )
        assert len(results) == 2
        rb1 = next(r for r in results if r[0] == "rb-1")
        rb2 = next(r for r in results if r[0] == "rb-2")
        # rb-1 has semantic + causal + component, no bm25
        assert rb1[1].bm25 == 0.0
        assert rb1[1].semantic == 0.8
        # rb-2 has only bm25
        assert rb2[1].semantic == 0.0
        assert rb2[1].bm25 == 1.0

"""Unit tests for BM25RunbookIndex - lexical retrieval over runbook summaries"""

import pytest
from deployd.adapters.outgoing.vector_store.bm25_index import BM25RunbookIndex, SparseHit

RUNBOOKS = [
    ("rb-libssl", "libssl.so.3 symbol not found after upgrade"),
    ("rb-oom", "process killed by OOM killer memory exhaustion"),
    ("rb-timeout", "upstream api timeout connection refused gateway error"),
]


class TestBM25Build:
    def test_search_before_build_raises(self) -> None:
        index = BM25RunbookIndex()
        with pytest.raises(RuntimeError, match="build should be called first"):
            index.search("libssl", top_k=3)

    def test_build_with_empty_corpus_search_returns_empty(self) -> None:
        index = BM25RunbookIndex()
        index.build([])
        results = index.search("anything", top_k=3)
        assert results == []


class TestBM25Search:
    def setup_method(self) -> None:
        self.index = BM25RunbookIndex()
        self.index.build(RUNBOOKS)

    def test_exact_keyword_match_ranks_first(self) -> None:
        results = self.index.search("libssl", top_k=3)
        assert results[0].runbook_id == "rb-libssl"

    def test_top_hit_has_highest_score(self) -> None:
        """Top hit should have the highest normalized score."""
        results = self.index.search("libssl symbol not found", top_k=3)
        assert results[0].runbook_id == "rb-libssl"
        assert results[0].bm25_score == pytest.approx(1.0)

    def test_top_k_limits_results(self) -> None:
        results = self.index.search("error", top_k=1)
        assert len(results) == 1

    def test_case_insensitive(self) -> None:
        results = self.index.search("LIBSSL", top_k=3)
        assert results[0].runbook_id == "rb-libssl"

    def test_no_match_returns_zero_scores(self) -> None:
        results = self.index.search("kubernetes helm chart", top_k=3)
        # all scores should be 0 since no words match
        for hit in results:
            assert hit.bm25_score == pytest.approx(0.0)

    def test_multi_word_query(self) -> None:
        results = self.index.search("OOM killer memory", top_k=3)
        assert results[0].runbook_id == "rb-oom"

    def test_returns_sparse_hit_type(self) -> None:
        results = self.index.search("timeout", top_k=1)
        assert isinstance(results[0], SparseHit)

    def test_scores_in_zero_one_range(self) -> None:
        results = self.index.search("timeout api", top_k=3)
        for hit in results:
            assert 0.0 <= hit.bm25_score <= 1.0

    def test_sorted_descending(self) -> None:
        results = self.index.search("api error", top_k=3)
        scores = [h.bm25_score for h in results]
        assert scores == sorted(scores, reverse=True)

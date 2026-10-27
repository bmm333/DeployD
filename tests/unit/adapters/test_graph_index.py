"""Unit tests for GraphIndex - structural similarity (LCS + Jaccard)"""

import pytest
from deployd.adapters.outgoing.vector_store.graph_index import (
    GraphIndex,
    _longest_common_subsequence_length,
)
from deployd.adapters.outgoing.vector_store.graph_store import GraphStore, RunbookStructure

# ── LCS (the algo underneath causal chain similarity) ──


class TestLCS:
    def test_identical_sequences(self) -> None:
        assert _longest_common_subsequence_length(("a", "b", "c"), ("a", "b", "c")) == 3

    def test_completely_different(self) -> None:
        assert _longest_common_subsequence_length(("a", "b"), ("x", "y")) == 0

    def test_subsequence_not_substring(self) -> None:
        # "a","c" is a subsequence of both even though not contiguous
        assert _longest_common_subsequence_length(("a", "b", "c"), ("a", "x", "c")) == 2

    def test_one_empty(self) -> None:
        assert _longest_common_subsequence_length((), ("a", "b")) == 0
        assert _longest_common_subsequence_length(("a",), ()) == 0

    def test_both_empty(self) -> None:
        assert _longest_common_subsequence_length((), ()) == 0

    def test_single_match(self) -> None:
        assert _longest_common_subsequence_length(("a",), ("a",)) == 1

    def test_order_matters(self) -> None:
        # "b","a" reversed — only 1 element can match in order
        assert _longest_common_subsequence_length(("a", "b"), ("b", "a")) == 1


# ── Causal chain similarity (static method) ──


class TestCausalChainSimilarity:
    def test_identical_chains_score_one(self) -> None:
        score = GraphIndex._causal_chain_similarity(("deploy", "crash"), ("deploy", "crash"))
        assert score == pytest.approx(1.0)

    def test_no_overlap_score_zero(self) -> None:
        score = GraphIndex._causal_chain_similarity(("deploy",), ("restart",))
        assert score == pytest.approx(0.0)

    def test_empty_chain_returns_zero(self) -> None:
        assert GraphIndex._causal_chain_similarity((), ("deploy",)) == 0.0
        assert GraphIndex._causal_chain_similarity(("deploy",), ()) == 0.0
        assert GraphIndex._causal_chain_similarity((), ()) == 0.0

    def test_partial_match_normalized(self) -> None:
        # LCS of ("deploy","crash","restart") vs ("deploy","restart") = 2 ("deploy","restart")
        # normalized by max(3, 2) = 3 → 2/3
        score = GraphIndex._causal_chain_similarity(
            ("deploy", "crash", "restart"), ("deploy", "restart")
        )
        assert score == pytest.approx(2 / 3)


# ── Component overlap (Jaccard, static method) ──


class TestComponentOverlap:
    def test_identical_sets_score_one(self) -> None:
        s = frozenset({"api", "db"})
        assert GraphIndex._component_overlap(s, s) == pytest.approx(1.0)

    def test_disjoint_sets_score_zero(self) -> None:
        assert GraphIndex._component_overlap(
            frozenset({"api"}), frozenset({"db"})
        ) == pytest.approx(0.0)

    def test_both_empty_returns_zero(self) -> None:
        assert GraphIndex._component_overlap(frozenset(), frozenset()) == 0.0

    def test_partial_overlap(self) -> None:
        # intersection = {"api"}, union = {"api","db","cache"} → 1/3
        a = frozenset({"api", "db"})
        b = frozenset({"api", "cache"})
        assert GraphIndex._component_overlap(a, b) == pytest.approx(1 / 3)

    def test_subset_overlap(self) -> None:
        # {"api"} ⊂ {"api","db"} → intersection=1, union=2 → 0.5
        assert GraphIndex._component_overlap(
            frozenset({"api"}), frozenset({"api", "db"})
        ) == pytest.approx(0.5)


# ── GraphIndex.search (integration with GraphStore) ──


class TestGraphIndexSearch:
    def _store_with_runbooks(self) -> GraphStore:
        store = GraphStore()
        store.add(
            RunbookStructure(
                runbook_id="rb-exact",
                causal_chain=("deploy", "crash", "restart"),
                affected_components=frozenset({"api", "db"}),
            )
        )
        store.add(
            RunbookStructure(
                runbook_id="rb-partial",
                causal_chain=("deploy", "timeout"),
                affected_components=frozenset({"api", "cache"}),
            )
        )
        store.add(
            RunbookStructure(
                runbook_id="rb-unrelated",
                causal_chain=("config_change",),
                affected_components=frozenset({"monitoring"}),
            )
        )
        return store

    def test_exact_match_ranked_first(self) -> None:
        index = GraphIndex(self._store_with_runbooks())
        hits = index.search(
            current_causal_chain=("deploy", "crash", "restart"),
            current_components=frozenset({"api", "db"}),
            top_k=3,
        )
        assert hits[0].runbook_id == "rb-exact"
        assert hits[0].causal_score == pytest.approx(1.0)
        assert hits[0].component_score == pytest.approx(1.0)

    def test_sorted_by_causal_score_descending(self) -> None:
        index = GraphIndex(self._store_with_runbooks())
        hits = index.search(
            current_causal_chain=("deploy", "crash", "restart"),
            current_components=frozenset({"api"}),
            top_k=3,
        )
        causal_scores = [h.causal_score for h in hits]
        assert causal_scores == sorted(causal_scores, reverse=True)

    def test_top_k_limits_results(self) -> None:
        index = GraphIndex(self._store_with_runbooks())
        hits = index.search(
            current_causal_chain=("deploy",),
            current_components=frozenset(),
            top_k=1,
        )
        assert len(hits) == 1

    def test_empty_store_returns_empty(self) -> None:
        index = GraphIndex(GraphStore())
        hits = index.search(("deploy",), frozenset({"api"}), top_k=5)
        assert hits == []

    def test_unrelated_runbook_gets_low_scores(self) -> None:
        index = GraphIndex(self._store_with_runbooks())
        hits = index.search(
            current_causal_chain=("deploy", "crash", "restart"),
            current_components=frozenset({"api", "db"}),
            top_k=3,
        )
        unrelated = next(h for h in hits if h.runbook_id == "rb-unrelated")
        assert unrelated.causal_score == pytest.approx(0.0)
        assert unrelated.component_score == pytest.approx(0.0)

"""Integration tests for hybrid runbook retrieval with structural context & hard-negative separation."""

import json
from pathlib import Path

import pytest
from deployd.adapters.outgoing.vector_store.bm25_index import BM25RunbookIndex
from deployd.adapters.outgoing.vector_store.chroma_client import ChromaRunbookClient
from deployd.adapters.outgoing.vector_store.graph_index import GraphIndex
from deployd.adapters.outgoing.vector_store.graph_store import GraphStore, RunbookStructure
from deployd.adapters.outgoing.vector_store.runbook_repository import JSONRunbookRepository
from deployd.adapters.outgoing.vector_store.similarity import blend
from deployd.application.dtos.retrieval import RetrievedEvidence

RUNBOOKS_DIR = Path("data/runbooks")
TEST_QUERIES_FILE = RUNBOOKS_DIR / "test_queries.json"


def load_test_queries() -> list[dict]:
    if not TEST_QUERIES_FILE.exists():
        return []
    with open(TEST_QUERIES_FILE, encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture(scope="module")
def loaded_runbooks() -> list[dict]:
    files = sorted(list(RUNBOOKS_DIR.glob("rb_*.json")))
    runbooks = []
    for f in files:
        with open(f, encoding="utf-8") as file:
            runbooks.append(json.load(file))
    return runbooks


@pytest.fixture(scope="module")
def retrieval_system(loaded_runbooks: list[dict], tmp_path_factory: pytest.TempPathFactory):
    tmp_path = tmp_path_factory.mktemp("chroma_db")

    bm25 = BM25RunbookIndex()
    bm25.build([(rb["runbook_id"], rb["summary"]) for rb in loaded_runbooks])

    graph_store = GraphStore()
    for rb in loaded_runbooks:
        graph_store.add(
            RunbookStructure(
                runbook_id=rb["runbook_id"],
                causal_chain=tuple(rb["causal_chain"]),
                affected_components=frozenset(rb["affected_components"]),
            )
        )
    graph_index = GraphIndex(graph_store)

    chroma = ChromaRunbookClient(persist_directory=str(tmp_path))
    for rb in loaded_runbooks:
        chroma.index_runbook(
            runbook_id=rb["runbook_id"],
            summary=rb["summary"],
            metadata={"affected_components": ",".join(rb["affected_components"])},
        )

    repo = JSONRunbookRepository(runbooks_dir=RUNBOOKS_DIR)

    return chroma, bm25, graph_index, repo


class TestRunbookRetrievalBenchmark:
    @pytest.mark.parametrize("query_data", load_test_queries())
    def test_query_retrieves_expected_runbook_with_structural_context(
        self, query_data: dict, retrieval_system: tuple
    ) -> None:
        chroma, bm25, graph_index, repo = retrieval_system

        query_text = query_data["query"]
        expected_runbook_id = query_data.get("expected_runbook_id")
        causal_chain = tuple(query_data.get("causal_chain", []))
        components = frozenset(query_data.get("affected_components", []))

        dense_hits = chroma.search(query_text, top_k=5)
        sparse_hits = bm25.search(query_text, top_k=5)
        struct_hits = graph_index.search(
            current_causal_chain=causal_chain,
            current_components=components,
            top_k=5,
        )

        results = blend(dense_hits, sparse_hits, struct_hits)
        assert results, f"Retrieval yielded no results for query: '{query_text}'"

        # Verify RetrievedEvidence DTO construction
        top_r_id, score_set, final_score = results[0]
        detail = repo.get_by_id(top_r_id)
        evidence = RetrievedEvidence(
            runbook_id=top_r_id,
            incident_id=detail.incident_id if detail else "",
            summary=detail.summary if detail else "",
            score=final_score,
            historical_root_cause=detail.root_cause if detail else None,
            historical_fix=detail.fix if detail else None,
            fix_commands=detail.fix_commands if detail else [],
        )

        assert evidence.runbook_id == top_r_id
        assert evidence.historical_root_cause is not None
        assert evidence.historical_fix is not None

        if expected_runbook_id:
            assert (
                top_r_id == expected_runbook_id
            ), f"Query '{query_text}' expected {expected_runbook_id}, but got {top_r_id}"

    def test_hard_negative_causal_chain_penalizes_false_component_match(
        self, retrieval_system: tuple
    ) -> None:
        """auth-service degraded (config change, no crash) must rank CONFIG-ROLLBACK above OOMKILL."""
        chroma, bm25, graph_index, repo = retrieval_system

        query_text = "auth-service degraded but never crashed after a config change"
        causal_chain = ("CONFIG_CHANGE", "HEALTH_CHECK_FAIL")
        components = frozenset(["auth-service"])

        dense_hits = chroma.search(query_text, top_k=5)
        sparse_hits = bm25.search(query_text, top_k=5)
        struct_hits = graph_index.search(
            current_causal_chain=causal_chain,
            current_components=components,
            top_k=5,
        )

        results = blend(dense_hits, sparse_hits, struct_hits)
        top1_id = results[0][0]

        assert top1_id == "RB-AUTH-SERVICE-CONFIG-ROLLBACK"
        # Verify OOMKill is ranked lower despite sharing the auth-service component
        oomkill_rank = [i for i, r in enumerate(results) if r[0] == "RB-AUTH-SERVICE-OOMKILL"][0]
        assert oomkill_rank > 0

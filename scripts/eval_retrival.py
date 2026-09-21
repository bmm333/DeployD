"""
Runs hybrid retrieval evaluation against test_queries.json using real structural context
(causal_chain and affected_components) and outputs recall@k with a full 4-signal score breakdown:
  - Semantic similarity (dense)
  - BM25 score (sparse)
  - Causal graph chain match
  - Component overlap
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

from deployd.adapters.outgoing.vector_store.bm25_index import BM25RunbookIndex
from deployd.adapters.outgoing.vector_store.chroma_client import ChromaRunbookClient
from deployd.adapters.outgoing.vector_store.graph_index import GraphIndex
from deployd.adapters.outgoing.vector_store.graph_store import GraphStore, RunbookStructure
from deployd.adapters.outgoing.vector_store.runbook_repository import JSONRunbookRepository
from deployd.adapters.outgoing.vector_store.similarity import blend_linear, blend_rrf
from deployd.application.dtos.retrieval import RetrievedEvidence

ROOT = Path(__file__).parent.parent
RUNBOOKS_DIR = ROOT / "data" / "runbooks"
CHROMA_DIR = str(ROOT / "data" / "chroma")
QUERIES_FILE = RUNBOOKS_DIR / "test_queries.json"

K = 3


def load_runbooks() -> list[dict]:
    runbooks = []
    for f in sorted(RUNBOOKS_DIR.glob("rb_*.json")):
        with open(f) as fh:
            runbooks.append(json.load(fh))
    return runbooks


def load_queries() -> list[dict]:
    with open(QUERIES_FILE) as f:
        return json.load(f)


def build_system_adapters(runbooks: list[dict]):
    chroma = ChromaRunbookClient(persist_directory=CHROMA_DIR)

    bm25 = BM25RunbookIndex()
    bm25.build([(rb["runbook_id"], rb["summary"]) for rb in runbooks])

    store = GraphStore()
    for rb in runbooks:
        store.add(
            RunbookStructure(
                runbook_id=rb["runbook_id"],
                causal_chain=tuple(rb["causal_chain"]),
                affected_components=frozenset(rb["affected_components"]),
            )
        )
    graph_index = GraphIndex(store)
    repo = JSONRunbookRepository(runbooks_dir=RUNBOOKS_DIR)

    return chroma, bm25, graph_index, repo


def process_results(results, repo, expected):
    retrieved_evidence_list: list[RetrievedEvidence] = []
    for r_id, _score_set, score in results[:K]:
        detail = repo.get_by_id(r_id)
        evidence = RetrievedEvidence(
            runbook_id=r_id,
            incident_id=detail.incident_id if detail else "",
            summary=detail.summary if detail else "",
            score=score,
            historical_root_cause=detail.root_cause if detail else None,
            historical_fix=detail.fix if detail else None,
            fix_commands=detail.fix_commands if detail else [],
        )
        retrieved_evidence_list.append(evidence)

    returned_ids = [e.runbook_id for e in retrieved_evidence_list]
    is_hit = expected is not None and expected in returned_ids
    return returned_ids, is_hit


def main() -> None:
    runbooks = load_runbooks()
    queries = load_queries()
    chroma, bm25, graph_index, repo = build_system_adapters(runbooks)

    by_category_lin: dict[str, list[bool]] = defaultdict(list)
    by_category_rrf: dict[str, list[bool]] = defaultdict(list)
    total_hits_lin = 0
    total_hits_rrf = 0

    print(
        f"\nEvaluating {len(queries)} queries with REAL STRUCTURAL CONTEXT (recall@{K})\n"
        + "=" * 80
    )

    for q in queries:
        query_text = q["query"]
        expected = q.get("expected_runbook_id")
        category = q.get("tests", "uncategorized")

        causal_chain = tuple(q.get("causal_chain", []))
        components = frozenset(q.get("affected_components", []))

        dense_hits = chroma.search(query_text, top_k=5)
        sparse_hits = bm25.search(query_text, top_k=5)
        struct_hits = graph_index.search(
            current_causal_chain=causal_chain,
            current_components=components,
            top_k=5,
        )

        results_lin = blend_linear(dense_hits, sparse_hits, struct_hits)
        results_rrf = blend_rrf(dense_hits, sparse_hits, struct_hits, k=60)

        ids_lin, hit_lin = process_results(results_lin, repo, expected)
        ids_rrf, hit_rrf = process_results(results_rrf, repo, expected)

        total_hits_lin += int(hit_lin)
        by_category_lin[category].append(hit_lin)

        total_hits_rrf += int(hit_rrf)
        by_category_rrf[category].append(hit_rrf)

        print(f"QUERY:    {query_text}")
        print(f"INPUTS:   chain={list(causal_chain)} | components={list(components)}")
        print(f"EXPECTED: {expected}")

        top1_lin = results_lin[0] if results_lin else None
        if top1_lin:
            top1_id, score_set, score = top1_lin
            top1_str = f"{top1_id:<32} (Sem: {score_set.semantic:.2f} | BM25: {score_set.bm25:.2f} | Caus: {score_set.causal:.2f} | Comp: {score_set.component:.2f} -> Score: {score:.3f})"
        else:
            top1_str = "NONE"
        print(f"[LIN] TOP-1: {top1_str}")
        print(f"[LIN] TOP-{K}: {ids_lin} | RESULT: {'Y HIT' if hit_lin else 'N MISS'}")

        top1_rrf = results_rrf[0] if results_rrf else None
        if top1_rrf:
            top1_id, score_set, score = top1_rrf
            top1_str_rrf = f"{top1_id:<32} (Sem: {score_set.semantic:.2f} | BM25: {score_set.bm25:.2f} | Caus: {score_set.causal:.2f} | Comp: {score_set.component:.2f} -> RRF: {score:.3f})"
        else:
            top1_str_rrf = "NONE"
        print(f"[RRF] TOP-1: {top1_str_rrf}")
        print(f"[RRF] TOP-{K}: {ids_rrf} | RESULT: {'Y HIT' if hit_rrf else 'N MISS'}")

        print("-" * 80)

    print(f"\nrecall@{K} by category [LINEAR]:")
    for cat, hits in by_category_lin.items():
        n = len(hits)
        h = sum(hits)
        print(f"  {cat:<35}: {h}/{n}  ({h / n:.2f})")

    print(f"\nrecall@{K} by category [RRF]:")
    for cat, hits in by_category_rrf.items():
        n = len(hits)
        h = sum(hits)
        print(f"  {cat:<35}: {h}/{n}  ({h / n:.2f})")

    total = len(queries)
    print(f"\nrecall@{K} TOTAL [LINEAR]: {total_hits_lin}/{total} ({total_hits_lin / total:.2f})")
    print(f"recall@{K} TOTAL [RRF]:    {total_hits_rrf}/{total} ({total_hits_rrf / total:.2f})")
    print("\n[VERIFIED] Evaluation completed for both Linear and RRF fusion.")


if __name__ == "__main__":
    main()

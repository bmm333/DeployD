"""DID-16: HybridRetriever — fused dense + sparse + structural retrieval over runbooks.

Combines ChromaDB (semantic/dense), BM25 (lexical/sparse) and, when a
``GraphIndex`` is injected and the caller supplies structural context, the
causal-chain LCS and component-Jaccard signals.  All four are fused by the
weighted blend in similarity.py (ADR-007).

Without structural context the CAUSAL and COMPONENT weights contribute 0.0,
so the best achievable fused score is SEMANTIC_WEIGHT + BM25_WEIGHT = 0.55 —
barely above the default 0.5 gate.  Live investigations therefore pass the
causal chain event types and affected components of the IncidentGraph: an
identical chain in the same order is the strongest signal the system has,
which is the point of a graph-native RAG over a generic one.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from deployd.adapters.outgoing.vector_store.similarity import ScoreSet, blend
from deployd.application.dtos.retrieval import RetrievalCandidate, RetrievalResult

if TYPE_CHECKING:
    from deployd.adapters.outgoing.vector_store.bm25_index import BM25RunbookIndex
    from deployd.adapters.outgoing.vector_store.chroma_client import ChromaRunbookClient
    from deployd.adapters.outgoing.vector_store.graph_index import GraphIndex, StructuralHit

logger = logging.getLogger(__name__)


class HybridRetriever:
    """Fused retriever: Chroma (dense) + BM25 (sparse) + GraphIndex (structural).

    Parameters
    ----------
    chroma:
        Initialised and indexed ChromaRunbookClient.
    bm25:
        Initialised and built BM25RunbookIndex.
    confidence_threshold:
        Minimum fused score for a candidate to be considered a strong match.
        Default 0.5 matches the orchestrator's tier-boundary check.
    graph_index:
        Optional GraphIndex over runbook structures.  When omitted, or when a
        query carries no structural context, only dense + sparse are fused.
    """

    def __init__(
        self,
        chroma: ChromaRunbookClient,
        bm25: BM25RunbookIndex,
        confidence_threshold: float = 0.5,
        graph_index: GraphIndex | None = None,
    ) -> None:
        self._chroma = chroma
        self._bm25 = bm25
        self._threshold = confidence_threshold
        self._graph_index = graph_index

    @property
    def confidence_threshold(self) -> float:
        return self._threshold

    def retrieve(
        self,
        query: str,
        top_k: int = 5,
        causal_chain: tuple[str, ...] = (),
        components: frozenset[str] = frozenset(),
    ) -> RetrievalResult:
        """Run a hybrid search and return a RetrievalResult.

        Parameters
        ----------
        query:
            Free-text query derived from the incident context.
        top_k:
            Maximum number of candidates to return before threshold filtering.
        causal_chain:
            Ordered event types of the incident's causal chain (structural signal).
        components:
            Components affected by the incident (structural signal).

        Returns
        -------
        RetrievalResult with candidates sorted by descending fused score.
        """
        result, _ = self.retrieve_scored(query, top_k, causal_chain, components)
        return result

    def retrieve_scored(
        self,
        query: str,
        top_k: int = 5,
        causal_chain: tuple[str, ...] = (),
        components: frozenset[str] = frozenset(),
    ) -> tuple[RetrievalResult, dict[str, ScoreSet]]:
        """Like ``retrieve`` but also returns the per-signal breakdown by runbook ID.

        The breakdown lets callers explain *why* a candidate did or did not
        clear the gate (e.g. the decision trace shown to the engineer).
        """
        logger.debug(
            "HybridRetriever.retrieve: query=%r top_k=%d chain=%s components=%s",
            query,
            top_k,
            causal_chain,
            sorted(components),
        )

        dense_hits = self._chroma.search(query, top_k=top_k)
        sparse_hits = self._bm25.search(query, top_k=top_k)
        structural_hits: list[StructuralHit] = []
        if self._graph_index is not None and (causal_chain or components):
            structural_hits = self._graph_index.search(
                current_causal_chain=causal_chain,
                current_components=components,
                top_k=top_k,
            )

        fused = blend(dense_hits, sparse_hits, structural_hits)[:top_k]

        # BM25 yields numpy floats; cast so results stay plain-Python (JSON-safe).
        candidates = [
            RetrievalCandidate(runbook_id=runbook_id, score=float(final_score))
            for runbook_id, _score_set, final_score in fused
        ]
        breakdown = {
            runbook_id: ScoreSet(
                semantic=float(s.semantic),
                bm25=float(s.bm25),
                causal=float(s.causal),
                component=float(s.component),
            )
            for runbook_id, s, _ in fused
        }

        logger.info(
            "HybridRetriever retrieved %d candidates (threshold=%.2f, strong=%d, structural=%s)",
            len(candidates),
            self._threshold,
            sum(1 for c in candidates if c.score >= self._threshold),
            bool(structural_hits),
        )

        return (
            RetrievalResult(candidates=candidates, confidence_threshold=self._threshold),
            breakdown,
        )

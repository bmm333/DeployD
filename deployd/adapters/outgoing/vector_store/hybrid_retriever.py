"""DID-16: HybridRetriever — fused dense + sparse retrieval over runbooks.

Combines ChromaDB (semantic/dense) and BM25 (lexical/sparse) scores using the
existing weighted blend from similarity.py.  GraphIndex is intentionally
excluded for this iteration — the causal/component signals require pre-indexed
graphs per runbook which are not yet available.  The semantic+BM25 fusion is
sufficient to match known failure patterns (e.g. OOMKill scenario → rb_auth_service_oom).

The weighting used here is a simplified two-signal subset of the full blend
defined in similarity.py.  We pass empty structural_hits so the CAUSAL and
COMPONENT weights contribute 0.0, which effectively redistributes weight to
semantic and BM25 in proportion to their declared weights:
    effective_semantic ≈ 0.35 / (0.35 + 0.20) = 63%
    effective_bm25     ≈ 0.20 / (0.35 + 0.20) = 36%

This is acceptable for the PoC.  RRF and full four-signal fusion are future work.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from deployd.adapters.outgoing.vector_store.similarity import blend
from deployd.application.dtos.retrieval import RetrievalCandidate, RetrievalResult

if TYPE_CHECKING:
    from deployd.adapters.outgoing.vector_store.bm25_index import BM25RunbookIndex
    from deployd.adapters.outgoing.vector_store.chroma_client import ChromaRunbookClient

logger = logging.getLogger(__name__)


class HybridRetriever:
    """Fused retriever: Chroma (dense) + BM25 (sparse), blended by similarity.py weights.

    Parameters
    ----------
    chroma:
        Initialised and indexed ChromaRunbookClient.
    bm25:
        Initialised and built BM25RunbookIndex.
    confidence_threshold:
        Minimum fused score for a candidate to be considered a strong match.
        Default 0.5 matches the orchestrator's tier-boundary check.
    """

    def __init__(
        self,
        chroma: ChromaRunbookClient,
        bm25: BM25RunbookIndex,
        confidence_threshold: float = 0.5,
    ) -> None:
        self._chroma = chroma
        self._bm25 = bm25
        self._threshold = confidence_threshold

    def retrieve(self, query: str, top_k: int = 5) -> RetrievalResult:
        """Run a hybrid search and return a RetrievalResult.

        Parameters
        ----------
        query:
            Free-text query derived from the incident context.
        top_k:
            Maximum number of candidates to return before threshold filtering.

        Returns
        -------
        RetrievalResult with candidates sorted by descending fused score.
        """
        logger.debug("HybridRetriever.retrieve: query=%r top_k=%d", query, top_k)

        dense_hits = self._chroma.search(query, top_k=top_k)
        sparse_hits = self._bm25.search(query, top_k=top_k)

        # Pass empty structural hits — GraphIndex not yet available.
        fused = blend(dense_hits, sparse_hits, structural_hits=[])

        candidates = [
            RetrievalCandidate(runbook_id=runbook_id, score=final_score)
            for runbook_id, _score_set, final_score in fused[:top_k]
        ]

        logger.info(
            "HybridRetriever retrieved %d candidates (threshold=%.2f, strong=%d)",
            len(candidates),
            self._threshold,
            sum(1 for c in candidates if c.score >= self._threshold),
        )

        return RetrievalResult(
            candidates=candidates,
            confidence_threshold=self._threshold,
        )

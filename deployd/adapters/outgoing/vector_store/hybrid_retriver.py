"""Hybrid Retriver DID-7
Combining dense (ChromDB) and sparse (BM25) retrival over historical runbooks, constrained by metadata from the deterministic engine
with an explicit confidece score so the pipeline never hands the agent an unssported match.

This is just the skeleton, nowhere the finished implementation.
Two decision are openended

1. Embedding model chocie. is not decided yet `all-MiniLM-L6-v2` is a good candidate, but this needs
to be verified by benchamrking against others.
2. Blend weights for combinating semantic score and bm25 score into final score. Cannto justify untill we
see with our own eyyes how it performs.

"""

from dataclasses import dataclass
from typing import Any

# da scegliere embedding model
# from sentence_transformers import SentenceTransformer
# from rank_bm25 import BM25Okapi
# import chromadb
from deployd.application.dtos.retrival import RetrivedEvidence


@dataclass(frozen=True)
class RetrivalConfig:
    top_k: int
    confidece_threshold: float
    semantic_weight: float
    bm25_weight: float
    embedding_model_name: str = "all-MiniLM-L6-v2"


class HybridRetriver:
    """Retrives hist runbook evidence for a given incident query,
    constrained by structured metadata from IncidentGraph
    Never returns a result below confidence threshold
    callers must treat an empty list as no known historical fix, not retry or lower the bar themselfes
    """

    def __init__(self, config: RetrivalConfig, chroma_collection: Any, bm25_corpus: Any) -> None:
        self._config = config
        self._chroma_collection = chroma_collection
        self.bm25_corpus = bm25_corpus
        # should load embedding model here as well

    def retrive(
        self, query: str, metadata_filter: dict[str, Any] | None = None
    ) -> list[RetrivedEvidence]:
        """returns the ranked retrived evidence, [] if no match found, meta data filter comes deterministic engine."""

        dense_hits = self._dense_search(query, metadata_filter)
        sparse_hits = self._sparse_search(query, metadata_filter)
        blended = self._blend_scores(dense_hits, sparse_hits)

        return [
            evidence
            for evidence in blended
            if evidence.final_score >= self._config.confidece_threshold
        ][: self._config.top_k]

    def _dense_search(
        self, query: str, metadata_Filter: dict[str, Any] | None
    ) -> list[tuple[str, float]]:
        raise NotImplementedError()

    def _sparse_search(
        self, query: str, metadata_filter: dict[str, Any] | None
    ) -> list[tuple[str, float]]:
        raise NotImplementedError

    def _blend_scores(
        self,
        dense_hits: list[tuple[str, float]],
        sparse_hits: list[tuple[str, float]],
    ) -> list[RetrivedEvidence]:
        raise NotImplementedError

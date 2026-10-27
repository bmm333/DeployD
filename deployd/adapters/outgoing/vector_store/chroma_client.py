"""wrapper for chromadb
for dense retrival on runbooks.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any

import chromadb
from sentence_transformers import SentenceTransformer

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DenseHit:
    runbook_id: str
    semantic_score: float  # for cos similarity [0,1]


class ChromaRunbookClient:
    COLLECTION_NAME = "runbooks"

    def __init__(
        self, persist_directory: str, embedding_model_name: str = "all-MiniLM-L6-v2"
    ) -> None:
        chroma_host = os.getenv("CHROMA_HOST")
        if chroma_host:
            self._client = chromadb.HttpClient(
                host=chroma_host,
                port=int(os.getenv("CHROMA_PORT", "8000")),
            )
        else:
            abs_path = os.path.abspath(persist_directory).replace("\\", "/")
            os.makedirs(abs_path, exist_ok=True)
            self._client = chromadb.PersistentClient(path=abs_path)
        self._collection = self._open_cosine_collection()
        self._model = SentenceTransformer(embedding_model_name)

    def _open_cosine_collection(self) -> Any:
        config: Any = {"hnsw": {"space": "cosine"}}
        collection = self._client.get_or_create_collection(
            self.COLLECTION_NAME, configuration=config
        )
        space = (collection.configuration_json.get("hnsw") or {}).get("space")
        if space != "cosine":
            logger.warning(
                "Chroma collection %r uses %r distance; recreating it with cosine. "
                "Re-index runbooks (scripts/seed_runbooks.py) if nothing else does.",
                self.COLLECTION_NAME,
                space,
            )
            self._client.delete_collection(self.COLLECTION_NAME)
            collection = self._client.create_collection(self.COLLECTION_NAME, configuration=config)
        return collection

    def index_runbook(self, runbook_id: str, summary: str, metadata: dict[str, Any]) -> None:
        """
        Embed and store one runbook summary text, metadata useful for `where`
        """
        embedding = self._model.encode(summary).tolist()
        self._collection.upsert(
            ids=[runbook_id], embeddings=[embedding], documents=[summary], metadatas=[metadata]
        )

    def search(
        self, query: str, top_k: int, metadata_filter: dict[str, Any] | None = None
    ) -> list[DenseHit]:
        query_embedding = self._model.encode(query).tolist()
        results = self._collection.query(
            query_embeddings=[query_embedding],
            n_results=top_k,
            where=metadata_filter or None,
        )
        # cosine distance in [0, 2]; the collection is forced to cosine space
        hits: list[DenseHit] = []
        ids = results.get("ids", [[]])[0]
        distances = results.get("distances", [[]])[0]
        for runbook_id, distance in zip(ids, distances, strict=False):
            similarity_score = max(0.0, 1.0 - distance)
            hits.append(DenseHit(runbook_id=runbook_id, semantic_score=similarity_score))
        return hits

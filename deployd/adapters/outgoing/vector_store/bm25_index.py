"""lexial retrival over historical runbooks via bm25
this part is introduced because most dense embeddings miss exact identifiers that bm25 catches by design
"""

from dataclasses import dataclass

from rank_bm25 import BM25Okapi


@dataclass(frozen=True)
class SparseHit:
    runbook_id: str
    bm25_score: float  # nromalized 0,1


class BM25RunbookIndex:
    """in mem bm25 index over runbook summaries(course scope)"""

    def __init__(self) -> None:
        self._runbook_ids: list[str] = []
        self._bm25: BM25Okapi | None = None

    def build(self, runbooks: list[tuple[str, str]]) -> None:
        """runbooks list of runbookid summary text"""
        self._runbook_ids = [rid for rid, _ in runbooks]
        tokenized = [summary.lower().split() for _, summary in runbooks]
        self._bm25 = BM25Okapi(tokenized)

    def search(self, query: str, top_k: int) -> list[SparseHit]:
        if self._bm25 is None:
            raise RuntimeError("BM25RunbookIndex build should be called first")
        tokenized_query = query.lower().split()
        raw_scores = self._bm25.get_scores(tokenized_query)
        max_score = max(raw_scores) if len(raw_scores) and max(raw_scores) > 0 else 1.0
        normalized = [score / max_score for score in raw_scores]

        ranked = sorted(
            zip(self._runbook_ids, normalized, strict=False), key=lambda pair: pair[1], reverse=True
        )
        return [SparseHit(runbook_id=rid, bm25_score=score) for rid, score in ranked[:top_k]]

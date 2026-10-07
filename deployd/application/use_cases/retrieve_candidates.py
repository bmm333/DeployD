"""DID-16: RetrieveCandidates — application use case wrapping HybridRetriever.

Thin orchestration layer: builds the query, delegates to the retriever,
returns the RetrievalResult.  No business logic lives here.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from deployd.application.dtos.retrieval import RetrievalResult
    from deployd.application.ports.retrieval_port import RetrievalPort


class RetrieveCandidates:
    """Use case: retrieve historical runbook candidates for an incident query.

    Parameters
    ----------
    retriever:
        Concrete RetrievalPort (injected; never imported here directly).
    """

    def __init__(self, retriever: RetrievalPort) -> None:
        self._retriever = retriever

    def execute(self, query: str, top_k: int = 5) -> RetrievalResult:
        """Run a hybrid retrieval query.

        Parameters
        ----------
        query:
            Free-text description of the incident, used as retrieval input.
        top_k:
            Maximum number of candidates to return.
        """
        return self._retriever.retrieve(query=query, top_k=top_k)

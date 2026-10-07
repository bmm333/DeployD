"""Port for the runbook retrieval service."""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from deployd.application.dtos.retrieval import RetrievalResult

@runtime_checkable
class RetrievalPort(Protocol):
    """Port for retrieving runbook candidates."""

    @property
    def confidence_threshold(self) -> float: ...

    def retrieve_scored(
        self,
        query: str,
        top_k: int = 5,
        causal_chain: tuple[str, ...] = (),
        components: frozenset[str] = frozenset(),
    ) -> tuple[RetrievalResult, dict[str, Any]]:
        """Retrieve runbook candidates scored by semantic and structural relevance."""
        ...

    def retrieve(
        self,
        query: str,
        top_k: int = 5,
        causal_chain: tuple[str, ...] = (),
        components: frozenset[str] = frozenset(),
    ) -> RetrievalResult:
        """Legacy retrieval (semantic only, or default logic)."""
        ...

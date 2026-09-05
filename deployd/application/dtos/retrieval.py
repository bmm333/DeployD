"""
DTOs for retrieval candidates and evidence.

DID-12: Retrieval result contract used by the InvestigationOrchestrator.

The HybridRetriever (DID-7) produces a list of RetrievalCandidates together
with the confidence threshold that was active at query time.  Bundling both
into RetrievalResult lets the orchestrator apply the tier-boundary check in a
single property call without re-specifying the threshold.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class RetrievedEvidence:
    """Represents a matched historical runbook with score breakdown and solution details."""

    runbook_id: str
    incident_id: str
    summary: str
    score: float
    historical_root_cause: str | None = None
    historical_fix: str | None = None
    fix_commands: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class RetrievalCandidate:
    """
    A single historical runbook match returned by the HybridRetriever.

    `score` is the fused similarity score [0.0, 1.0] produced by the weighted
    combination of semantic (Chroma), BM25, causal-LCS, and component-Jaccard
    signals described in ADR-007.
    """

    runbook_id: str
    score: float
    reference_url: str | None = None


@dataclass(frozen=True)
class RetrievalResult:
    """
    Output of one HybridRetriever query, including the threshold used.

    `has_strong_match` is the single boolean the orchestrator needs to decide
    between Tier 2 and Tier 3.  A candidate whose score is exactly equal to
    `confidence_threshold` is considered a strong match (>=).

    An empty `candidates` list is a valid result — it means the retriever ran
    successfully but found nothing above the minimum pre-filter.
    """

    candidates: list[RetrievalCandidate] = field(default_factory=list)
    confidence_threshold: float = 0.5

    @property
    def has_strong_match(self) -> bool:
        """True when at least one candidate meets or exceeds the threshold."""
        return any(c.score >= self.confidence_threshold for c in self.candidates)

    @property
    def strong_candidates(self) -> list[RetrievalCandidate]:
        """All candidates that meet or exceed the confidence threshold."""
        return [c for c in self.candidates if c.score >= self.confidence_threshold]

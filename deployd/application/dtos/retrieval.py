"""DTOs for retrieval candidates and evidence."""
"""
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

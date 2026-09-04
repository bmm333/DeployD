"""DTOs for retrieval candidates and evidence."""

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

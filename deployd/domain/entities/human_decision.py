"""
HumanDecision captures a human engineer's verdict on an AI-generated diagnosis.

Safety boundary:
  HumanDecision is an immutable fact record.  It records what a human decided
  and nothing else. It has no execution capability and triggers no side-effects
  beyond what the application layer chooses to do with it: approving a
  recommendation never executes it (the system suggests, engineers approve).
"""

from __future__ import annotations

import uuid  # noqa: TCH003  # pydantic needs it at runtime
from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class DecisionValue(str, Enum):
    """What the engineer did with the agent's answer."""

    ACCEPT = "ACCEPT"  # root cause and recommendation approved as proposed
    REJECT = "REJECT"  # rejected with a reason; the agent is asked to revise
    AMEND = "AMEND"  # approved with an edited recommendation


# ---------------------------------------------------------------------------
# Entity
# ---------------------------------------------------------------------------


class HumanDecision(BaseModel):  # type: ignore[misc]  # BaseModel is Any under disallow_any_explicit
    """Immutable record of an engineer's decision on one version of the agent's answer.

    ``diagnosis_version`` is 1 for the diagnosis and increments with every revision
    the agent makes after a rejection.  The reviewed answer (root cause,
    recommendation, citations, model, prompt version) is copied in, so the audit
    trail does not depend on the chat history.
    """

    model_config = ConfigDict(frozen=True)

    incident_id: uuid.UUID
    decision: DecisionValue
    engineer_id: str
    timestamp: datetime
    root_cause: str
    recommendation: str
    comment: str | None = None
    amended_recommendation: str | None = None
    diagnosis_version: int = 1
    evidence_references: list[str] = Field(default_factory=list)
    model: str | None = None
    prompt_version: str | None = None

    # ---------------------------------------------------------------------------
    # Validators
    # ---------------------------------------------------------------------------

    @field_validator("timestamp")
    @classmethod
    def timestamp_must_be_utc(cls, v: datetime) -> datetime:
        """All timestamps must be timezone-aware UTC."""
        if v.tzinfo is None:
            raise ValueError("timestamp must be timezone-aware (UTC)")
        return v.astimezone(timezone.utc)

    @field_validator("diagnosis_version")
    @classmethod
    def version_must_be_positive(cls, v: int) -> int:
        if v < 1:
            raise ValueError("diagnosis_version must be >= 1")
        return v

    @field_validator("engineer_id")
    @classmethod
    def engineer_id_must_be_non_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("engineer_id must not be blank")
        return v.strip()

    @model_validator(mode="after")
    def decision_carries_what_it_needs(self) -> HumanDecision:
        if self.decision is DecisionValue.REJECT and not (self.comment or "").strip():
            raise ValueError("a rejection needs a reason (comment)")
        amended = (self.amended_recommendation or "").strip()
        if self.decision is DecisionValue.AMEND and not amended:
            raise ValueError("an amendment needs the amended recommendation")
        if self.decision is not DecisionValue.AMEND and self.amended_recommendation is not None:
            raise ValueError("only an amendment carries an amended recommendation")
        return self

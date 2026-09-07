"""
HumanDecision captures a human engineer's verdict on an AI-generated diagnosis.

Safety boundary:
  HumanDecision is an immutable fact record.  It records what a human decided
  and nothing else. It has no execution capability and triggers no side-effects
  beyond what the application layer chooses to do with it.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import uuid

from pydantic import BaseModel, ConfigDict, field_validator

# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class DecisionValue(str, Enum):
    """The set of choices available to an engineer reviewing a diagnosis."""

    ACCEPT = "ACCEPT"
    REJECT = "REJECT"
    REQUEST_EVIDENCE = "REQUEST_EVIDENCE"


# ---------------------------------------------------------------------------
# Entity
# ---------------------------------------------------------------------------


class HumanDecision(BaseModel):  # type: ignore[misc]
    """Immutable record of an engineer's decision on an AI diagnosis.

    Decisions are facts — once recorded they must not be mutated.
    If a subsequent decision is needed (e.g. after more evidence is gathered)
    a new HumanDecision with an incremented diagnosis_version is created.
    """

    model_config = ConfigDict(frozen=True)

    investigation_id: uuid.UUID
    decision: DecisionValue
    engineer_id: str
    timestamp: datetime
    comment: str | None = None
    # Tracks which version of the diagnosis was under review.
    # Starts at 1; increments each time the AI re-diagnoses after REQUEST_EVIDENCE.
    diagnosis_version: int = 1

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
        return v

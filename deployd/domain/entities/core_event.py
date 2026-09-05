"""
DID-2
CoreEvent is the domain rappresentation of an observed event. Every external adapter must
translate their specific formats into core event before entering domain.
Domain never depends on any external or any infra awarnes. CoreEvent can speak only in domain semantics.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone  # noqa: TCH003
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, field_validator


class CoreEventType(str, Enum):
    """
    Domain level event semantic
    Rappresents what happend, not how it was observed
    `segmentation fault(core dumped)` is a log msg
    PROCESS_CRASH is our domain concept
    """

    # Deployment lifecycle
    DEPLOY_STARTED = "DEPLOY_STARTED"
    DEPLOY_COMPLETED = "DEPLOY_COMPLETED"
    DEPLOY_FAILED = "DEPLOY_FAILED"
    # Process failures
    PROCESS_CRASH = "PROCESS_CRASH"
    DEPENDENCY_FAILURE = "DEPENDENCY_FAILURE"
    RESOURCE_EXHAUSTION = "RESOURCE_EXHAUSTION"
    # Infra state
    CONFIG_CHANGE = "CONFIG_CHANGE"
    CONNECTIVITY_LOSS = "CONNECTIVITY_LOSS"
    STATE_CHANGE = "STATE_CHANGE"
    HEALTH_CHECK_FAIL = "HEALTH_CHECK_FAIL"
    HEALTH_CHECK_PASS = "HEALTH_CHECK_PASS"
    # Human triggered
    HUMAN_OBSERVATION = "HUMAN_OBSERVATION"
    # Detection-derived (DID-9): inferred by a DetectionRule from evidence + reference
    # data, not observed directly by an adapter.
    CONFIG_DRIFT_DETECTED = "CONFIG_DRIFT_DETECTED"


class Severity(str, Enum):
    """Domain understandment of serverity"""

    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


class CoreEvent(BaseModel):  # type: ignore[misc]
    """Immutable rappresentation of an event (domain rappresentation)
    Events are facts not mutable states
    """

    model_config = ConfigDict(frozen=True)
    event_id: uuid.UUID = Field(default_factory=uuid.uuid4)
    event_type: CoreEventType
    timestamp: datetime
    severity: Severity
    related_component: str | None = None
    description: str
    metadata: dict[str, object] = Field(default_factory=dict)

    @field_validator("timestamp")
    @classmethod
    def must_be_utc(cls, v: datetime) -> datetime:
        """all time stamps must be timezone aware UTC."""
        if v.tzinfo is None:
            raise ValueError("Timestamp must be timezone-aware (UTC)")
        return v.astimezone(timezone.utc)

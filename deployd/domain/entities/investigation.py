"""
Investigation is the central stateful domain aggregate of DeployD.

It tracks the full lifecycle of an incident investigation: from creation
through evidence collection, diagnosis, and human decision.

Safety boundary:
  Investigation carries data and recommendations only.
  No execution methods, no production-system side-effects,
  no automated remediation capability exists here.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from pydantic import BaseModel, ConfigDict, Field, field_validator

from deployd.domain.entities.core_event import CoreEvent  # noqa: TCH001
from deployd.domain.graph.graph import IncidentGraph

# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class InvestigationStatus(str, Enum):
    """Lifecycle states of an Investigation.

    Transitions must follow the legal graph enforced by
    Investigation.transition_to().
    """

    BUILDING = "BUILDING"
    RETRIEVING = "RETRIEVING"
    DIAGNOSING = "DIAGNOSING"
    WAITING_FOR_EVIDENCE = "WAITING_FOR_EVIDENCE"
    COMPLETED = "COMPLETED"


class TriggerType(str, Enum):
    """What caused this investigation to be opened."""

    MANUAL = "MANUAL"
    ALERT = "ALERT"
    SCHEDULED = "SCHEDULED"


# ---------------------------------------------------------------------------
# Legal status transitions
# ---------------------------------------------------------------------------

_LEGAL_TRANSITIONS: dict[InvestigationStatus, set[InvestigationStatus]] = {
    InvestigationStatus.BUILDING: {
        InvestigationStatus.RETRIEVING,
        InvestigationStatus.DIAGNOSING,
        InvestigationStatus.COMPLETED,
    },
    InvestigationStatus.RETRIEVING: {
        InvestigationStatus.DIAGNOSING,
        InvestigationStatus.WAITING_FOR_EVIDENCE,
        InvestigationStatus.COMPLETED,
    },
    InvestigationStatus.DIAGNOSING: {
        InvestigationStatus.WAITING_FOR_EVIDENCE,
        InvestigationStatus.COMPLETED,
    },
    InvestigationStatus.WAITING_FOR_EVIDENCE: {
        InvestigationStatus.RETRIEVING,
        InvestigationStatus.DIAGNOSING,
        InvestigationStatus.COMPLETED,
    },
    InvestigationStatus.COMPLETED: set(),  # terminal state
}


# ---------------------------------------------------------------------------
# Domain exceptions
# ---------------------------------------------------------------------------


class InvestigationError(Exception):
    """Base class for Investigation domain errors."""


class IllegalStatusTransitionError(InvestigationError):
    """Raised when a status transition violates the lifecycle rules."""


class InvalidOperationError(InvestigationError):
    """Raised when a domain method is called in the wrong status."""


# ---------------------------------------------------------------------------
# Aggregate
# ---------------------------------------------------------------------------


class Investigation(BaseModel):  # type: ignore[misc]
    """Central stateful aggregate tracking one incident investigation.

    Investigation is deliberately mutable (frozen=False) because it is a
    long-lived aggregate whose state evolves over time.  Domain invariants
    are enforced via the transition_to() guard and named domain methods,
    not by immutability.

    Persistence note:
      IncidentGraph is not a Pydantic model; serialisation of the graph
      is the responsibility of the repository adapter, not this entity.
    """

    model_config = ConfigDict(
        arbitrary_types_allowed=True,  # IncidentGraph is not a Pydantic model
        frozen=False,
    )

    investigation_id: uuid.UUID = Field(default_factory=uuid.uuid4)
    trigger_type: TriggerType
    started_at: datetime
    status: InvestigationStatus = InvestigationStatus.BUILDING

    # Raw request payload that triggered this investigation.
    # Kept as dict so the application layer can pass any adapter-specific data.
    request: dict[str, object] = Field(default_factory=dict)

    # Graph of causally-related events built by the CausalEngine.
    incident_graph: IncidentGraph | None = None

    # Raw CoreEvents collected as evidence during the investigation.
    evidence: list[CoreEvent] = Field(default_factory=list)

    # Runbook / playbook retrieval hits produced by the hybrid retrieval system (ADR-007).
    # Kept as list[dict] because the retrieval schema is defined in the adapter layer.
    retrieved_evidence: list[dict[str, object]] = Field(default_factory=list)

    # AI-generated diagnosis output.  Opaque dict for now; will be promoted
    # to a typed value object when the AI layer is built (DID-7).
    diagnosis: dict[str, object] | None = None

    # Human-readable summary of the investigation outcome.
    outcome: str | None = None

    # ---------------------------------------------------------------------------
    # Validators
    # ---------------------------------------------------------------------------

    @field_validator("started_at")
    @classmethod
    def started_at_must_be_utc(cls, v: datetime) -> datetime:
        """All timestamps must be timezone-aware UTC."""
        if v.tzinfo is None:
            raise ValueError("started_at must be timezone-aware (UTC)")
        return v.astimezone(timezone.utc)

    # ---------------------------------------------------------------------------
    # Domain methods — lifecycle management
    # ---------------------------------------------------------------------------

    def transition_to(self, new_status: InvestigationStatus) -> None:
        """Move the investigation to *new_status* if the transition is legal.

        Raises:
            IllegalStatusTransitionError: if the transition is not in the
                allowed set for the current status.
        """
        allowed = _LEGAL_TRANSITIONS[self.status]
        if new_status not in allowed:
            raise IllegalStatusTransitionError(
                f"Cannot transition from {self.status.value} to {new_status.value}. "
                f"Allowed targets: {[s.value for s in allowed]}"
            )
        self.status = new_status

    def attach_graph(self, graph: IncidentGraph) -> None:
        """Attach the causal incident graph built during the BUILDING phase."""
        self.incident_graph = graph

    def add_evidence(self, event: CoreEvent) -> None:
        """Append a raw CoreEvent to the evidence list."""
        self.evidence.append(event)

    def add_retrieved_evidence(self, hit: dict[str, object]) -> None:
        """Append a retrieval hit (runbook candidate) to retrieved_evidence."""
        self.retrieved_evidence.append(hit)

    def set_diagnosis(self, data: dict[str, object]) -> None:
        """Record the AI-generated diagnosis.

        Must only be called while status is DIAGNOSING.

        Raises:
            InvalidOperationError: if called in any other status.
        """
        if self.status != InvestigationStatus.DIAGNOSING:
            raise InvalidOperationError(
                f"set_diagnosis() requires status DIAGNOSING, got {self.status.value}"
            )
        self.diagnosis = data

    def complete(self, outcome: str) -> None:
        """Finalise the investigation with a human-readable outcome string.

        Transitions status to COMPLETED.

        Raises:
            IllegalStatusTransitionError: if the current status does not
                allow transitioning to COMPLETED.
        """
        self.transition_to(InvestigationStatus.COMPLETED)
        self.outcome = outcome

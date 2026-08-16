"""
DD-1
DTOs for evidence collected during an investigation.

``EvidenceDTO``  — a single piece of collected evidence (system event, human note, FSM transition).
``MissingEvidence`` — a gap the agent identifies that needs to be filled before a firm conclusion.

Agents receive ``List[EvidenceDTO]`` and return ``List[MissingEvidence]`` — never raw domain objects.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field

from deployd.application.dtos.enums import EvidenceSource


class EvidenceDTO(BaseModel):
    """
    A single piece of evidence collected and validated before being handed to an agent.

    Confidence must be a float in [0.0, 1.0].  A value of 0.0 means "completely
    uncertain"; 1.0 means "absolute certainty" (e.g. a machine-generated FSM log).
    """

    model_config = ConfigDict(frozen=True)

    evidence_id: str = Field(
        ...,
        description="Unique identifier for this evidence item (UUID string).",
        examples=["7c9e6679-7425-40de-944b-e07fc1f90ae7"],
    )
    source: EvidenceSource = Field(
        ...,
        description="Origin of the evidence — system event, human note, or FSM transition.",
    )
    description: str = Field(
        ...,
        min_length=1,
        description="Human-readable description of what this evidence represents.",
        examples=["CPU usage hit 98% on node api-gateway-3 at 14:02 UTC"],
    )
    component: str = Field(
        ...,
        min_length=1,
        description="Name of the system component this evidence relates to.",
        examples=["api-gateway", "postgres-primary"],
    )
    timestamp: datetime = Field(
        ...,
        description="UTC timestamp when this evidence was observed or recorded.",
    )
    confidence: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Confidence level that this evidence is accurate and relevant (0.0–1.0).",
        examples=[0.95],
    )
    raw_data: dict[str, Any] = Field(
        default_factory=dict,
        description="Raw, unprocessed payload attached to this evidence item for traceability.",
    )


class MissingEvidence(BaseModel):
    """
    Describes a gap in the evidence chain that the agent was unable to fill.

    Returned by the diagnosis agent so that the orchestrator can schedule
    additional collection passes or escalate to a human.
    """

    model_config = ConfigDict(frozen=True)

    description: str = Field(
        ...,
        min_length=1,
        description="What is missing — expressed in plain language.",
        examples=["Kubernetes pod restart logs for auth-service between 14:00 and 14:10 UTC"],
    )
    why_needed: str = Field(
        ...,
        min_length=1,
        description="Explanation of why this evidence is required to confirm the hypothesis.",
        examples=["Without pod logs we cannot distinguish OOMKill from Liveness probe failure."],
    )
    source_incident_id: Optional[str] = Field(
        default=None,
        description="If this gap was inferred from a historical incident, its ID is recorded here.",
        examples=["INC-20240815-0042"],
    )
    collection_method: str = Field(
        ...,
        min_length=1,
        description="Suggested method to obtain the missing evidence.",
        examples=["kubectl logs -n prod auth-service --since=1h", "PagerDuty timeline export"],
    )

"""
DD-1
DTOs for the investigation request boundary.

Data flow:
    IncomingEvent (adapter) -> CoreEvent (domain) -> InvestigationRequest (DTO) -> Agent

Agents must ONLY receive this DTO, never a CoreEvent or database model.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field

from deployd.application.dtos.enums import TriggerType


class EventDTO(BaseModel):
    """
    DTO representation of an observed event before it is converted into a CoreEvent.

    This is what the outside world describes — it uses raw strings rather than
    domain-level ``CoreEventType`` so that the DTO layer stays decoupled.
    """

    model_config = ConfigDict(frozen=True)

    event_id: str = Field(
        ...,
        description="Unique identifier for the event (UUID string).",
        examples=["3fa85f64-5717-4562-b3fc-2c963f66afa6"],
    )
    timestamp: datetime = Field(
        ...,
        description="UTC timestamp when the event was observed.",
    )
    event_type: str = Field(
        ...,
        min_length=1,
        description="Raw event type string as received from the source adapter.",
        examples=["DEPLOY_FAILED", "PROCESS_CRASH"],
    )
    source: str = Field(
        ...,
        min_length=1,
        description="System or component that generated the event.",
        examples=["kubernetes-prod", "reporter-agent"],
    )
    payload: dict[str, Any] = Field(
        default_factory=dict,
        description="Arbitrary key/value metadata attached to the raw event.",
    )


class ComponentDependencyDTO(BaseModel):
    """
    Represents a directed dependency edge in the component topology graph.

    Used by agents to perform impact radius analysis.
    """

    model_config = ConfigDict(frozen=True)

    source_component: str = Field(
        ...,
        min_length=1,
        description="The upstream component in this dependency relationship.",
        examples=["api-gateway"],
    )
    target_component: str = Field(
        ...,
        min_length=1,
        description="The downstream component that depends on *source_component*.",
        examples=["auth-service"],
    )
    relationship: str = Field(
        ...,
        min_length=1,
        description="Semantic label for the dependency (e.g. 'HTTP', 'gRPC', 'DB').",
        examples=["HTTP", "gRPC", "message-queue"],
    )


class InvestigationRequest(BaseModel):
    """
    Top-level DTO passed to an AI agent to initiate an investigation.

    Agents must never receive a ``CoreEvent`` or any domain/infrastructure object —
    only this DTO.  All enrichment (component graph, ranked events) is materialised
    here by the application mapper before crossing the agent boundary.
    """

    model_config = ConfigDict(frozen=True)

    trigger_type: TriggerType = Field(
        ...,
        description="Whether the investigation was auto-detected or engineer-triggered.",
    )
    human_description: Optional[str] = Field(
        default=None,
        description="Free-text description provided by an engineer (only when ENGINEER_TRIGGERED).",
    )
    events: list[EventDTO] = Field(
        ...,
        min_length=1,
        description="Ordered list of events that form the evidence window for this investigation.",
    )
    affected_components: list[str] = Field(
        ...,
        description="Components that are confirmed or suspected to be involved.",
        examples=[["api-gateway", "auth-service"]],
    )
    dependency_map: list[ComponentDependencyDTO] = Field(
        default_factory=list,
        description="Snapshot of the relevant dependency graph edges at investigation time.",
    )
    requested_by: Optional[str] = Field(
        default=None,
        description="Identity of the engineer who triggered the investigation, if applicable.",
        examples=["alice@example.com"],
    )
    timestamp: datetime = Field(
        ...,
        description="UTC timestamp when the investigation was created.",
    )

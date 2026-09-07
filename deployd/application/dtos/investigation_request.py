"""
DD-1
DTOs for the investigation request boundary.

Data flow:
    IncomingEvent (adapter) -> CoreEvent (domain) -> InvestigationRequest (DTO) -> Agent

Agents must ONLY receive this DTO, never a CoreEvent or database model.

Note on naming
--------------
``InvestigationRequest`` (dataclass) — the legacy orchestrator contract used by
the three-tier InvestigationOrchestrator.run() pipeline (DID-12).

``AgentInvestigationRequest`` (Pydantic) — the richer DTO materialised by the
ADR-008 pipeline mappers and handed to AI agents.  It is exported from the
package ``__init__`` simply as ``InvestigationRequest`` for external callers
that import from ``deployd.application.dtos``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any, Optional

from pydantic import BaseModel, ConfigDict, Field

from deployd.application.dtos.enums import TriggerType

if TYPE_CHECKING:
    from deployd.application.dtos.retrieval import RetrievalResult
    from deployd.domain.graph.graph import IncidentGraph
    from deployd.domain.health.process_state import ProcessHealthStatus


# ---------------------------------------------------------------------------
# Legacy three-tier orchestrator contract (DID-12)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class InvestigationRequest:
    """
    Input to InvestigationOrchestrator.run() (three-tier pipeline, DID-12).

    `component`        — identifier of the service / process being investigated.
    `graph`            — IncidentGraph built from observed CoreEvents for the
                         query window; may be empty (Tier-1 path).
    `fsm_state`        — current ProcessHealthStatus of `component` after
                         replaying all events through ProcessHealthFSM.
    `retrieval_result` — output of the HybridRetriever query for this incident;
                         pass RetrievalResult() (empty, default threshold) when
                         no retrieval was performed.
    """

    component: str
    graph: IncidentGraph
    fsm_state: ProcessHealthStatus
    retrieval_result: RetrievalResult


# ---------------------------------------------------------------------------
# ADR-008 pipeline DTOs (new design)
# ---------------------------------------------------------------------------


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


class AgentInvestigationRequest(BaseModel):
    """
    Top-level DTO passed to an AI agent to initiate an investigation (ADR-008).

    Agents must never receive a ``CoreEvent`` or any domain/infrastructure object —
    only this DTO.  All enrichment (component graph, ranked events) is materialised
    here by the application mapper before crossing the agent boundary.

    This class is exported from ``deployd.application.dtos`` as
    ``InvestigationRequest`` so that external callers use a stable name.
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

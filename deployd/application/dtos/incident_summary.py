"""
ADR-008
Intermediate DTO: synthesised incident narrative produced by the orchestrator.

Used as a stepping-stone inside the pipeline:

    IncidentGraph + CoreEvents
        → (EventMapper)
            → InvestigationRequest
                → (agent)
                    → IncidentSummaryDTO     ← assembled by orchestrator
                        → DiagnosisRequest
                            → (diagnosis agent)
                                → DiagnosisResult

``IncidentSummaryDTO`` never touches ChromaDB internals, ORM models, or domain
objects — it is a pure application-layer value object.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from deployd.application.dtos.enums import TriggerType


class IncidentSummaryDTO(BaseModel):
    """
    Synthesised incident narrative assembled by the orchestrator.

    The orchestrator constructs this from the first-pass agent response and
    embeds it in a ``DiagnosisRequest`` so the diagnosis agent has a concise,
    pre-digested picture of the incident instead of raw event lists.
    """

    model_config = ConfigDict(frozen=True)

    investigation_id: str = Field(
        ...,
        description="Unique ID of the in-flight investigation session.",
        examples=["INV-20260816-0001"],
    )
    narrative: str = Field(
        ...,
        min_length=1,
        description=(
            "Human-readable summary of the incident synthesised by the orchestrator. "
            "Should describe what happened, when, and which components were affected."
        ),
        examples=[
            "api-gateway returned 502 for 8 minutes after the deploy of v3.1.0 at 14:00 UTC. "
            "auth-service was restarted twice during the window."
        ],
    )
    affected_components: list[str] = Field(
        ...,
        min_length=1,
        description="Ordered list of component names confirmed or suspected to be involved.",
        examples=[["api-gateway", "auth-service"]],
    )
    trigger_type: TriggerType = Field(
        ...,
        description="How the investigation was initiated.",
    )
    human_context: str | None = Field(
        default=None,
        description=(
            "Optional free-text context provided by an engineer. "
            "Present only when trigger_type is ENGINEER_TRIGGERED."
        ),
        examples=["On-call engineer noticed 502s after the auth-service deploy."],
    )

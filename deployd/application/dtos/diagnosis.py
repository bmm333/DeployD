"""
DID-5 / DID-12 / ADR-008: Diagnosis-related DTOs.

This module contains two families of classes:

Legacy three-tier orchestrator DTOs (DID-12)
---------------------------------------------
``DiagnosisTier``              — which tier the orchestrator selected.
``TierRemediation``            — remediation attached to a TierDiagnosisResult.
``TierDiagnosisResult``        — the output of InvestigationOrchestrator.run().

ADR-008 diagnosis pipeline DTOs
---------------------------------
``AlternativeHypothesis``      — an alternative root-cause hypothesis.
``RemediationRecommendation``  — typed remediation plan (Pydantic, risk-gated).
``DiagnosisResult``            — the output of the diagnosis agent (Pydantic).
``DiagnosisRequest``           — the input to the diagnosis agent (Pydantic).
"""

from __future__ import annotations

from dataclasses import dataclass, field as dc_field
from enum import Enum
from typing import TYPE_CHECKING, Optional

from pydantic import BaseModel, ConfigDict, Field

from deployd.application.dtos.enums import RiskLevel, TriggerType
from deployd.application.dtos.evidence import EvidenceDTO, MissingEvidence
from deployd.application.dtos.retrieval import EvidenceReference, RetrievedEvidence

if TYPE_CHECKING:
    from deployd.domain.graph.node import GraphNode
    from deployd.domain.health.process_state import ProcessHealthStatus


# ===========================================================================
# Legacy three-tier orchestrator DTOs (DID-12)
# ===========================================================================


class DiagnosisTier(str, Enum):
    """
    Which evidence tier the orchestrator selected for this result.

    INCONCLUSIVE — Tier 1. FSM is healthy and the IncidentGraph is empty for
                   the query window.  No LLM was called; result is fully
                   deterministic.

    CHAIN_ONLY   — Tier 2. A real causal chain exists but the HybridRetriever
                   returned no candidate above the confidence threshold.  The
                   chain is surfaced to the engineer; no fix is suggested.

    FULL         — Tier 3. Chain exists and at least one historical runbook
                   matched above the confidence threshold.  Fix suggestion is
                   grounded in retrieved evidence and still requires human
                   approval before any tool execution.
    """

    INCONCLUSIVE = "INCONCLUSIVE"
    CHAIN_ONLY = "CHAIN_ONLY"
    FULL = "FULL"


@dataclass(frozen=True)
class TierRemediation:
    """
    Suggested remediation attached to every TierDiagnosisResult.

    The field is always populated — callers should never have to handle a
    missing recommendation.  For Tier-1 and Tier-2 results `summary` states
    explicitly why no fix can be provided, and `evidence_references` is empty.

    `requires_human_approval` is always True for Tier-2 and Tier-3 results;
    Tier-1 results also set it True because there is no actionable signal at
    all and a human must confirm the situation before any action is taken.
    """

    summary: str
    requires_human_approval: bool
    evidence_references: list[str] = dc_field(default_factory=list)


@dataclass(frozen=True)
class TierDiagnosisResult:
    """
    Immutable result of one three-tier investigation run.

    `causal_chains` is empty for Tier-1 results (no evidence, nothing to
    traverse).  For Tier-2 and Tier-3 it contains the paths returned by
    CausalEngine.causal_chain() from the root nodes of the IncidentGraph.
    """

    tier: DiagnosisTier
    fsm_state: ProcessHealthStatus
    causal_chains: list[list[GraphNode]]
    remediation: TierRemediation


# ===========================================================================
# ADR-008 diagnosis pipeline DTOs (new design)
# ===========================================================================


class AlternativeHypothesis(BaseModel):
    """
    An alternative root-cause hypothesis that the agent considered but
    ranked below the primary explanation.

    Included in ``DiagnosisResult`` so engineers can evaluate competing
    theories without re-running the investigation.
    """

    model_config = ConfigDict(frozen=True)

    explanation: str = Field(
        ...,
        min_length=1,
        description="Plain-language description of the alternative root cause.",
        examples=["Network partition caused cascading timeouts."],
    )
    confidence: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Agent's confidence that this hypothesis is the real root cause (0.0–1.0).",
        examples=[0.35],
    )
    supporting_evidence: list[str] = Field(
        default_factory=list,
        description="IDs of EvidenceDTO or RetrievedEvidence items that support this hypothesis.",
    )
    missing_evidence: list[str] = Field(
        default_factory=list,
        description="Descriptions of evidence that would be needed to confirm this hypothesis.",
    )


class RemediationRecommendation(BaseModel):
    """
    Typed remediation plan produced by the diagnosis agent (ADR-008).

    ``steps`` is always non-empty — at minimum the agent must include one
    actionable step (even if it is "escalate to on-call engineer").
    ``requires_human_approval`` is always True for HIGH/CRITICAL risk levels
    (enforced by ADR-008 §Remediation Rules via the orchestrator validator).
    """

    model_config = ConfigDict(frozen=True)

    summary: str = Field(
        ...,
        min_length=1,
        description="Concise summary of the recommended remediation action.",
        examples=["Roll back auth-service to v2.2.9 and apply memory-limit patch."],
    )
    steps: list[str] = Field(
        ...,
        min_length=1,
        description="Ordered list of concrete remediation steps. Must be non-empty.",
        examples=[["kubectl rollout undo deployment/auth-service -n prod"]],
    )
    risk_level: RiskLevel = Field(
        ...,
        description="Assessed operational risk of executing these steps.",
    )
    requires_human_approval: bool = Field(
        ...,
        description=(
            "Whether a human must explicitly approve before any step is executed. "
            "Must be True when risk_level is HIGH or CRITICAL (ADR-008)."
        ),
    )
    prerequisites: list[str] = Field(
        default_factory=list,
        description="Conditions or checks that must be satisfied before executing the steps.",
        examples=[["Confirm rollout is not currently in progress."]],
    )


class DiagnosisResult(BaseModel):
    """
    Immutable result of one diagnosis run (ADR-008 pipeline).

    Returned by the diagnosis agent and validated by
    ``InvestigationOrchestrator.validate_diagnosis_result`` before being
    handed back to the caller.

    ``evidence_references`` must be non-empty — the DTO enforces this via
    ``min_length=1`` and the orchestrator re-checks as a defence-in-depth.
    """

    model_config = ConfigDict(frozen=True)

    root_cause_explanation: str = Field(
        ...,
        min_length=1,
        description="Plain-language explanation of the diagnosed root cause.",
        examples=["Memory leak in auth-service v3.1.0 caused OOMKill under peak traffic."],
    )
    confidence: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Agent's overall confidence in this diagnosis (0.0–1.0).",
        examples=[0.88],
    )
    remediation: RemediationRecommendation = Field(
        ...,
        description="Recommended remediation plan for this diagnosis.",
    )
    evidence_references: list[EvidenceReference] = Field(
        ...,
        min_length=1,
        description=(
            "Non-empty list of EvidenceReference items that back the root-cause explanation. "
            "ADR-008 requires at least one reference for every diagnosis."
        ),
    )
    alternative_hypotheses: list[AlternativeHypothesis] = Field(
        default_factory=list,
        description="Alternative root-cause hypotheses that the agent considered but ranked lower.",
    )
    missing_evidence: list[MissingEvidence] = Field(
        default_factory=list,
        description="Gaps in evidence that prevented the agent from reaching a firm conclusion.",
    )
    unsupported_claims: list[str] = Field(
        default_factory=list,
        description=(
            "Agent assertions that could not be backed by any retrieved evidence. "
            "Preserved verbatim — the orchestrator does NOT drop them."
        ),
    )


class DiagnosisRequest(BaseModel):
    """
    DTO handed to the diagnosis agent at the input boundary (ADR-008).

    Assembled by ``InvestigationOrchestrator.build_diagnosis_request`` from an
    ``IncidentSummaryDTO`` and the RAG retrieval results.  The agent receives
    only this DTO — no domain objects, no ChromaDB internals.
    """

    model_config = ConfigDict(frozen=True)

    investigation_id: str = Field(
        ...,
        description="Unique ID of the in-flight investigation session.",
        examples=["INV-20260816-0001"],
    )
    incident_summary: str = Field(
        ...,
        min_length=1,
        description="Human-readable narrative of the incident synthesised by the orchestrator.",
        examples=["api-gateway returned 502 for 8 minutes after the deploy of v3.1.0 at 14:00 UTC."],
    )
    retrieved_evidence: list[RetrievedEvidence] = Field(
        default_factory=list,
        description="Ranked historical incidents from the RAG pipeline.",
    )
    available_evidence: list[EvidenceDTO] = Field(
        default_factory=list,
        description="Live evidence items converted from CoreEvent by the EventMapper.",
    )
    trigger_type: TriggerType = Field(
        ...,
        description="How the investigation was initiated.",
    )
    human_description: Optional[str] = Field(
        default=None,
        description=(
            "Free-text context provided by the triggering engineer. "
            "Present only when trigger_type is ENGINEER_TRIGGERED."
        ),
        examples=["On-call engineer noticed 502s after the auth-service deploy."],
    )

"""
DD-1
DTOs for the diagnosis boundary — the primary AI agent I/O contract.

Data flow (in):
    Application -> DiagnosisRequest -> Agno Detective Agent

Data flow (out):
    Agno Detective Agent -> DiagnosisResult -> Application Mapper -> Domain

The agent must ONLY accept ``DiagnosisRequest`` and return ``DiagnosisResult``.
No domain objects, ChromaDB types, or ORM models may cross this boundary.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from deployd.application.dtos.enums import RiskLevel, TriggerType
from deployd.application.dtos.evidence import EvidenceDTO, MissingEvidence
from deployd.application.dtos.retrieval import EvidenceReference, RetrievedEvidence


class RemediationRecommendation(BaseModel):
    """
    A concrete remediation plan produced by the diagnosis agent.

    ``risk_level`` must be set to ``HIGH`` or ``CRITICAL`` whenever
    ``requires_human_approval`` is ``True`` — enforcement is the mapper's
    responsibility but the DTO captures the intent.
    """

    model_config = ConfigDict(frozen=True)

    summary: str = Field(
        ...,
        min_length=1,
        description="One-sentence summary of the recommended remediation action.",
        examples=["Roll back auth-service to v2.2.9 and restart the pod."],
    )
    steps: list[str] = Field(
        ...,
        min_length=1,
        description="Ordered list of concrete steps the on-call engineer should execute.",
        examples=[["kubectl rollout undo deployment/auth-service -n prod", "Verify pod health"]],
    )
    risk_level: RiskLevel = Field(
        ...,
        description="Operational risk level of executing this remediation.",
    )
    prerequisites: list[str] = Field(
        default_factory=list,
        description="Conditions that must be true before executing the remediation.",
        examples=[["Backup of current config taken", "Change-freeze window not active"]],
    )
    evidence_references: list[EvidenceReference] = Field(
        default_factory=list,
        description="Historical incidents that support this remediation recommendation.",
    )
    requires_human_approval: bool = Field(
        ...,
        description=(
            "When True, the orchestrator must gate execution on explicit engineer approval "
            "before any automated action is taken."
        ),
    )


class AlternativeHypothesis(BaseModel):
    """
    A secondary root-cause hypothesis considered by the agent but not selected as primary.

    Providing alternatives allows engineers to quickly pivot if the primary hypothesis
    turns out to be incorrect.
    """

    model_config = ConfigDict(frozen=True)

    explanation: str = Field(
        ...,
        min_length=1,
        description="Plain-language description of this alternative root cause.",
        examples=["Network partition between api-gateway and auth-service caused cascading timeouts."],
    )
    confidence: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Agent's confidence in this alternative hypothesis (0.0–1.0).",
        examples=[0.35],
    )
    supporting_evidence: list[str] = Field(
        default_factory=list,
        description="Evidence IDs or descriptions that partially support this hypothesis.",
        examples=[["EVD-001", "EVD-004"]],
    )
    missing_evidence: list[str] = Field(
        default_factory=list,
        description="Evidence that, if collected, would confirm or rule out this hypothesis.",
        examples=[["Network latency metrics between api-gateway and auth-service"]],
    )


class DiagnosisRequest(BaseModel):
    """
    Everything the Agno Detective Agent needs to produce a ``DiagnosisResult``.

    Assembled by the application mapper from domain entities and RAG retrieval
    results.  The agent must not perform any DB queries — all context is embedded here.
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
        description="Concise narrative of what happened, synthesised by the orchestrator.",
        examples=["api-gateway returned 502 for 8 minutes after deploy of v3.1.0 at 14:00 UTC."],
    )
    retrieved_evidence: list[RetrievedEvidence] = Field(
        ...,
        description="Ranked historical incidents retrieved from ChromaDB by the RAG agent.",
    )
    available_evidence: list[EvidenceDTO] = Field(
        ...,
        description="Live evidence items collected for the current incident.",
    )
    trigger_type: TriggerType = Field(
        ...,
        description="How the investigation was initiated.",
    )
    human_description: Optional[str] = Field(
        default=None,
        description="Optional engineer-provided context (only when ENGINEER_TRIGGERED).",
    )


class DiagnosisResult(BaseModel):
    """
    The agent's conclusion after analysing ``DiagnosisRequest``.

    Every claim must be backed by at least one ``EvidenceReference``.
    Unsupported claims are surfaced separately so that the orchestrator can
    flag or discard them without silently trusting them.
    """

    model_config = ConfigDict(frozen=True)

    root_cause_explanation: str = Field(
        ...,
        min_length=1,
        description="Primary root-cause explanation selected by the agent.",
        examples=["Memory leak in auth-service v3.1.0 triggered OOMKill, causing 502s on api-gateway."],
    )
    confidence: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Agent's overall confidence in the primary root cause (0.0–1.0).",
        examples=[0.88],
    )
    remediation: RemediationRecommendation = Field(
        ...,
        description="Recommended remediation for the identified root cause.",
    )
    evidence_references: list[EvidenceReference] = Field(
        ...,
        min_length=1,
        description="Historical incidents that support the primary root-cause explanation.",
    )
    alternative_hypotheses: list[AlternativeHypothesis] = Field(
        default_factory=list,
        description="Other root causes considered but not selected as primary.",
    )
    missing_evidence: list[MissingEvidence] = Field(
        default_factory=list,
        description="Evidence gaps that prevented the agent from reaching higher confidence.",
    )
    unsupported_claims: list[str] = Field(
        default_factory=list,
        description=(
            "Any agent assertions that lack supporting evidence references. "
            "The orchestrator must treat these as speculative."
        ),
    )

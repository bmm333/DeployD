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
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from deployd.application.dtos.enums import RiskLevel, TriggerType
from deployd.application.dtos.evidence import EvidenceDTO, MissingEvidence
from deployd.application.dtos.incident_summary import IncidentSummaryDTO
from deployd.application.dtos.retrieval import EvidenceReference, RetrievedEvidence
from deployd.domain.graph.node import GraphNode  # noqa: TCH001
from deployd.domain.health.process_state import ProcessHealthStatus  # noqa: TCH001


class AgentDiagnosis(BaseModel):
    """Validated structured output schema for agent diagnoses.

    Used as ``response_model`` by the Agno agent.
    A deterministic evidence validator runs on top to strip any runbook IDs
    that the model hallucinated.
    """

    root_cause: str = Field(description="Concise identification of the root cause")
    confidence: str = Field(description="High, Medium, or Low")
    reasoning: str = Field(description="Step-by-step analysis of the evidence")
    recommendation: str = Field(description="Specific remediation action")
    evidence_references: list[str] = Field(
        default_factory=list,
        description="Runbook IDs cited as evidence (only IDs present in the system)",
    )


class AlternativeHypothesis(BaseModel):
    """An alternative hypothesis considered by the agent."""
    
    model_config = ConfigDict(frozen=True)

    explanation: str = Field(..., description="Explanation of the alternative hypothesis")
    confidence: float = Field(..., ge=0.0, le=1.0, description="Confidence level (0.0 to 1.0)")
    supporting_evidence: list[EvidenceReference] = Field(default_factory=list, description="Evidence supporting this hypothesis")
    missing_evidence: list[MissingEvidence] = Field(default_factory=list, description="Evidence missing that would confirm this hypothesis")


class RemediationRecommendation(BaseModel):
    """A recommended remediation plan."""
    
    model_config = ConfigDict(frozen=True)

    summary: str = Field(..., description="Summary of the remediation")
    steps: list[str] = Field(..., description="Ordered list of steps to resolve the issue")
    risk_level: RiskLevel = Field(..., description="Risk level associated with the remediation")
    prerequisites: list[str] = Field(default_factory=list, description="Prerequisites before executing the remediation")
    evidence_references: list[EvidenceReference] = Field(default_factory=list, description="Evidence supporting this remediation")
    requires_human_approval: bool = Field(..., description="Whether human approval is required before execution")


class DiagnosisResult(BaseModel):
    """The structured diagnosis result returned by the agent."""
    
    model_config = ConfigDict(frozen=True)

    root_cause_explanation: str = Field(..., description="Detailed explanation of the root cause")
    confidence: float = Field(..., ge=0.0, le=1.0, description="Confidence level (0.0 to 1.0)")
    remediation: RemediationRecommendation = Field(..., description="Recommended remediation action")
    evidence_references: list[EvidenceReference] = Field(default_factory=list, description="References to the evidence used")
    alternative_hypotheses: list[AlternativeHypothesis] = Field(default_factory=list, description="Alternative hypotheses considered")
    missing_evidence: list[MissingEvidence] = Field(default_factory=list, description="Evidence gaps identified during diagnosis")
    unsupported_claims: list[str] = Field(default_factory=list, description="Claims made without sufficient evidence")


class DiagnosisRequest(BaseModel):
    """The input to the diagnosis agent to investigate an incident."""
    
    model_config = ConfigDict(frozen=True)

    investigation_id: str = Field(..., description="Unique ID for this investigation")
    incident_summary: IncidentSummaryDTO = Field(..., description="Synthesised summary of the incident")
    retrieved_evidence: list[RetrievedEvidence] = Field(default_factory=list, description="Historical evidence retrieved from vector store")
    available_evidence: list[EvidenceDTO] = Field(default_factory=list, description="Currently available systemic evidence")
    trigger_type: TriggerType = Field(..., description="How the investigation was initiated")
    human_description: Optional[str] = Field(default=None, description="Optional description provided by the human triggering the investigation")


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
    remediation: RemediationRecommendation
    structured_diagnosis: AgentDiagnosis | None = None

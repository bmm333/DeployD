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

from pydantic import BaseModel, Field

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

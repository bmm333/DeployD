"""
DID-12: Output contract of the InvestigationOrchestrator.

DiagnosisResult is the single, typed value the orchestrator returns for every
investigation regardless of which tier applied.  Callers can branch on `tier`
without touching any internal orchestrator state.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from deployd.domain.graph.node import GraphNode  # noqa: TCH001
from deployd.domain.health.process_state import ProcessHealthStatus  # noqa: TCH001


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
class RemediationRecommendation:
    """
    Suggested remediation attached to every DiagnosisResult.

    The field is always populated — callers should never have to handle a
    missing recommendation.  For Tier-1 and Tier-2 results `summary` states
    explicitly why no fix can be provided, and `evidence_references` is empty.

    `requires_human_approval` is always True for Tier-2 and Tier-3 results;
    Tier-1 results also set it True because there is no actionable signal at
    all and a human must confirm the situation before any action is taken.
    """

    summary: str
    requires_human_approval: bool
    evidence_references: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class DiagnosisResult:
    """
    Immutable result of one investigation run.

    `causal_chains` is empty for Tier-1 results (no evidence, nothing to
    traverse).  For Tier-2 and Tier-3 it contains the paths returned by
    CausalEngine.causal_chain() from the root nodes of the IncidentGraph.
    """

    tier: DiagnosisTier
    fsm_state: ProcessHealthStatus
    causal_chains: list[list[GraphNode]]
    remediation: RemediationRecommendation

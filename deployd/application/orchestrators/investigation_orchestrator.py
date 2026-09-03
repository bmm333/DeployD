"""
DID-12: InvestigationOrchestrator — three-tier diagnostic orchestrator.

This is the single enforcement point for the "DeployD never hallucinates"
guarantee documented in the technical report (§4).

The orchestrator inspects evidence availability BEFORE deciding which tier
applies.  Tiers 1 and 2 are fully deterministic and never invoke the AI agent.
The agent is only reached in Tier 3, where every fix suggestion is grounded in
retrieved historical evidence and still requires human approval before any tool
execution occurs.

    Tier 1 — INCONCLUSIVE
        Condition: IncidentGraph has no nodes (no observable evidence).
        Action:    Return a deterministic result. Agent is NOT called.
                   Zero token cost, zero hallucination risk by construction.

    Tier 2 — CHAIN_ONLY
        Condition: Causal chain exists; HybridRetriever returned no candidate
                   above the confidence threshold.
        Action:    Expose the chain to the engineer. RemediationRecommendation
                   states explicitly that no known historical fix exists and
                   requires human approval. Agent is NOT called.

    Tier 3 — FULL
        Condition: Causal chain exists AND at least one retrieval candidate
                   meets or exceeds the confidence threshold.
        Action:    Delegate to AgentPort for a grounded diagnosis. The result
                   still carries requires_human_approval=True; no tool is
                   executed without explicit engineer sign-off.

AgentPort is a Protocol so the orchestrator never imports Agno directly.
The concrete Agno implementation is injected at construction time (DID-5/DID-7).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

from deployd.application.dtos.diagnosis import (
    DiagnosisResult,
    DiagnosisTier,
    RemediationRecommendation,
)
from deployd.domain.causal.causal_engine import CausalEngine

if TYPE_CHECKING:
    from deployd.application.dtos.investigation_request import InvestigationRequest
    from deployd.application.dtos.retrieval import RetrievalCandidate
    from deployd.domain.graph.node import GraphNode
    from deployd.domain.health.process_state import ProcessHealthStatus


# ── Agent port ────────────────────────────────────────────────────────────────


@runtime_checkable
class AgentPort(Protocol):
    """
    Seam between the orchestrator and the concrete AI agent (Agno, DID-5/7).

    The orchestrator only calls this in Tier 3.  Implementors must return a
    human-readable summary grounded in `candidates`; they must never fabricate
    information not present in the evidence or the retrieved runbooks.
    """

    def diagnose(
        self,
        component: str,
        causal_chains: list[list[GraphNode]],
        candidates: list[RetrievalCandidate],
    ) -> str:
        """Return a diagnosis summary grounded in evidence and candidates."""
        ...


# ── Orchestrator ──────────────────────────────────────────────────────────────


class InvestigationOrchestrator:
    """
    Application-layer orchestrator for incident investigation.

    Parameters
    ----------
    agent:
        Concrete implementation of AgentPort (injected; never imported here).
        Only called in Tier 3.  Pass a no-op stub in tests that cover Tiers 1/2.
    """

    def __init__(self, agent: AgentPort) -> None:
        self._agent = agent

    # ── Public API ────────────────────────────────────────────────────────────

    def run(self, request: InvestigationRequest) -> DiagnosisResult:
        """
        Inspect evidence availability and dispatch to the correct tier.

        The tier decision is made once, up front, before any agent code is
        reachable.  This is intentional: it makes the no-LLM guarantee
        structural, not prompt-based.
        """
        graph_is_empty = len(request.graph.nodes) == 0

        if graph_is_empty:
            return self._tier1_inconclusive(request.fsm_state)

        chains = self._build_chains(request)

        if not request.retrieval_result.has_strong_match:
            return self._tier2_chain_only(request.fsm_state, chains)

        return self._tier3_full(
            component=request.component,
            fsm_state=request.fsm_state,
            chains=chains,
            candidates=request.retrieval_result.strong_candidates,
        )

    # ── Tier implementations ──────────────────────────────────────────────────

    def _tier1_inconclusive(self, fsm_state: ProcessHealthStatus) -> DiagnosisResult:
        """
        Tier 1: no observable evidence in the query window.

        The FSM state is healthy and the graph is empty.  We have nothing to
        reason about, so we surface that fact honestly instead of guessing.
        The AI agent is NOT called.
        """
        return DiagnosisResult(
            tier=DiagnosisTier.INCONCLUSIVE,
            fsm_state=fsm_state,
            causal_chains=[],
            remediation=RemediationRecommendation(
                summary=(
                    "No observable events were recorded for this component in the "
                    "query window and the FSM reports a healthy state.  There is "
                    "insufficient evidence to diagnose a problem.  A human operator "
                    "should verify the component directly before taking any action."
                ),
                requires_human_approval=True,
                evidence_references=[],
            ),
        )

    def _tier2_chain_only(
        self,
        fsm_state: ProcessHealthStatus,
        chains: list[list[GraphNode]],
    ) -> DiagnosisResult:
        """
        Tier 2: causal chain exists but no historical runbook matched.

        The chain is shown to the engineer as-is.  No fix is invented.
        `requires_human_approval` is always True here — the agent was not
        called, so the human is the only source of a remediation decision.
        """
        return DiagnosisResult(
            tier=DiagnosisTier.CHAIN_ONLY,
            fsm_state=fsm_state,
            causal_chains=chains,
            remediation=RemediationRecommendation(
                summary=(
                    "A causal chain was identified in the incident graph but no "
                    "known historical fix exists in the runbook store above the "
                    "confidence threshold.  Human review of the chain is required "
                    "before any remediation action is taken."
                ),
                requires_human_approval=True,
                evidence_references=[],
            ),
        )

    def _tier3_full(
        self,
        component: str,
        fsm_state: ProcessHealthStatus,
        chains: list[list[GraphNode]],
        candidates: list[RetrievalCandidate],
    ) -> DiagnosisResult:
        """
        Tier 3: chain + strong historical match.

        The AI agent is called exactly once, with the causal chain and the
        retrieval candidates as grounding context.  The result still requires
        human approval before any tool execution.
        """
        summary = self._agent.diagnose(
            component=component,
            causal_chains=chains,
            candidates=candidates,
        )
        references = [c.runbook_id for c in candidates]

        return DiagnosisResult(
            tier=DiagnosisTier.FULL,
            fsm_state=fsm_state,
            causal_chains=chains,
            remediation=RemediationRecommendation(
                summary=summary,
                requires_human_approval=True,
                evidence_references=references,
            ),
        )

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _build_chains(self, request: InvestigationRequest) -> list[list[GraphNode]]:
        """
        Collect all causal chains from every root node in the IncidentGraph.

        Root nodes (nodes with no incoming edges) are the natural starting
        points for causal traversal — they represent the earliest observable
        events that have not themselves been caused by something else in the
        graph.
        """
        engine = CausalEngine(request.graph)
        chains: list[list[GraphNode]] = []
        for root in request.graph.get_root_nodes():
            chains.extend(engine.causal_chain(root.node_id))
        return chains

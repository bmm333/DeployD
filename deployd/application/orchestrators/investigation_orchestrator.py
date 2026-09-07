"""
ADR-008 / DID-12 (DD-17)
InvestigationOrchestrator — wires the investigation pipeline and enforces
the DTO boundary contracts.

Two overlapping responsibilities
---------------------------------
1. Three-tier diagnostic orchestrator (DID-12): ``run()`` method.
   Accepts an ``InvestigationRequest`` dataclass (component, graph, fsm_state,
   retrieval_result) and returns a ``TierDiagnosisResult`` after selecting
   the correct tier.  Tiers 1 and 2 are fully deterministic and NEVER call
   the AI agent.  The agent (``AgentPort``) is only reached in Tier 3.

2. ADR-008 pipeline factory (DD-17): ``build_*`` and ``validate_*`` methods.
   Accept domain objects from the use-case layer, call mappers to materialise
   all context into DTOs *before* the agent boundary, and validate
   ``DiagnosisResult`` objects coming *back* from the agent.

ADR-008 enforcement rules
--------------------------
* ``requires_human_approval=True`` → ``risk_level`` MUST be ``HIGH`` or
  ``CRITICAL``.  Any lower level is a boundary violation.
* ``evidence_references`` must be non-empty (already enforced by the DTO
  ``min_length=1``, but the orchestrator re-checks for defence-in-depth).
* ``unsupported_claims`` are preserved as-is and returned to the caller;
  the orchestrator does NOT silently drop them.

The orchestrator is intentionally agent-agnostic: it does not import any Agno
or LLM client.  The caller (use case / application service) is responsible for
actually invoking the agent and passing its result back here for validation.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from deployd.application.dtos.diagnosis import (
    DiagnosisRequest,
    DiagnosisResult,
    DiagnosisTier,
    TierDiagnosisResult,
    TierRemediation,
)
from deployd.application.dtos.enums import RiskLevel, TriggerType
from deployd.application.dtos.incident_summary import IncidentSummaryDTO
from deployd.application.dtos.retrieval import RetrievedEvidence
from deployd.application.mappers.event_mapper import EventMapper
from deployd.application.mappers.graph_mapper import GraphMapper
from deployd.domain.entities.core_event import CoreEvent
from deployd.domain.graph.graph import IncidentGraph

if TYPE_CHECKING:
    from deployd.application.dtos.investigation_request import InvestigationRequest, RetrievalCandidate
    from deployd.domain.graph.node import GraphNode
    from deployd.domain.health.process_state import ProcessHealthStatus

# Risk levels that satisfy the human-approval gate (ADR-008 §Remediation Rules)
_HIGH_RISK_LEVELS: frozenset[RiskLevel] = frozenset({RiskLevel.HIGH, RiskLevel.CRITICAL})


# ===========================================================================
# Agent port (DID-12 / DID-5 / DID-7)
# ===========================================================================


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


# ===========================================================================
# Boundary violation error (ADR-008)
# ===========================================================================


class BoundaryViolationError(Exception):
    """
    Raised when a ``DiagnosisResult`` violates an ADR-008 boundary contract.

    The message identifies the specific rule that was broken so that the
    calling use-case can log it, escalate, or surface it to the on-call engineer.
    """


# ===========================================================================
# Orchestrator
# ===========================================================================


class InvestigationOrchestrator:
    """
    Stateless pipeline orchestrator that covers two design phases:

    Phase 1 (DID-12): Three-tier diagnostic orchestrator.
        ``InvestigationOrchestrator(agent).run(request)`` — accepts an
        ``InvestigationRequest`` dataclass and returns a ``TierDiagnosisResult``
        after selecting the correct tier deterministically.

    Phase 2 (ADR-008): ADR-008 pipeline factory.
        ``InvestigationOrchestrator().build_investigation_request(...)`` etc. —
        factory methods that materialise domain objects into agent-safe DTOs.

    Both phases can be used independently or together.
    """

    def __init__(
        self,
        agent: AgentPort | None = None,
        event_mapper: EventMapper | None = None,
        graph_mapper: GraphMapper | None = None,
    ) -> None:
        self._agent = agent
        self._event_mapper = event_mapper or EventMapper()
        self._graph_mapper = graph_mapper or GraphMapper()

    # --------------------------------------------------------------------------
    # Phase 1: Three-tier orchestrator (DID-12)
    # --------------------------------------------------------------------------

    def run(self, request: InvestigationRequest) -> TierDiagnosisResult:
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

    def _tier1_inconclusive(self, fsm_state: ProcessHealthStatus) -> TierDiagnosisResult:
        """
        Tier 1: no observable evidence in the query window.

        The FSM state is healthy and the graph is empty.  We have nothing to
        reason about, so we surface that fact honestly instead of guessing.
        The AI agent is NOT called.
        """
        return TierDiagnosisResult(
            tier=DiagnosisTier.INCONCLUSIVE,
            fsm_state=fsm_state,
            causal_chains=[],
            remediation=TierRemediation(
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
    ) -> TierDiagnosisResult:
        """
        Tier 2: causal chain exists but no historical runbook matched.

        The chain is shown to the engineer as-is.  No fix is invented.
        `requires_human_approval` is always True here — the agent was not
        called, so the human is the only source of a remediation decision.
        """
        return TierDiagnosisResult(
            tier=DiagnosisTier.CHAIN_ONLY,
            fsm_state=fsm_state,
            causal_chains=chains,
            remediation=TierRemediation(
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
    ) -> TierDiagnosisResult:
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

        return TierDiagnosisResult(
            tier=DiagnosisTier.FULL,
            fsm_state=fsm_state,
            causal_chains=chains,
            remediation=TierRemediation(
                summary=summary,
                requires_human_approval=True,
                evidence_references=references,
            ),
        )

    def _build_chains(self, request: InvestigationRequest) -> list[list[GraphNode]]:
        """
        Collect all causal chains from every root node in the IncidentGraph.

        Root nodes (nodes with no incoming edges) are the natural starting
        points for causal traversal — they represent the earliest observable
        events that have not themselves been caused by something else in the
        graph.
        """
        from deployd.domain.causal.causal_engine import CausalEngine  # local to avoid cycle

        engine = CausalEngine(request.graph)
        chains: list[list[GraphNode]] = []
        for root in request.graph.get_root_nodes():
            chains.extend(engine.causal_chain(root.node_id))
        return chains

    # --------------------------------------------------------------------------
    # Phase 2: ADR-008 pipeline factory methods (input-boundary → DTO)
    # --------------------------------------------------------------------------

    def build_investigation_request(
        self,
        *,
        events: list[CoreEvent],
        graph: IncidentGraph,
        trigger_type: TriggerType,
        human_description: str | None = None,
        requested_by: str | None = None,
        timestamp: datetime | None = None,
    ) -> AgentInvestigationRequest:
        """
        Materialise all context from domain objects into an ``AgentInvestigationRequest``.

        This is the primary input-boundary crossing point.  After this call,
        the returned DTO — and only this DTO — may be handed to an AI agent.
        """
        from deployd.application.dtos.investigation_request import AgentInvestigationRequest

        event_dtos = self._event_mapper.core_events_to_event_dtos(events)
        dependency_map = self._graph_mapper.graph_to_dependency_map(graph)
        affected_components = self._graph_mapper.affected_components(graph)

        return AgentInvestigationRequest(
            trigger_type=trigger_type,
            human_description=human_description,
            events=event_dtos,
            affected_components=affected_components,
            dependency_map=dependency_map,
            requested_by=requested_by,
            timestamp=(timestamp or datetime.now(timezone.utc)).astimezone(timezone.utc),
        )

    def build_incident_summary(
        self,
        *,
        investigation_id: str,
        narrative: str,
        graph: IncidentGraph,
        trigger_type: TriggerType,
        human_context: str | None = None,
    ) -> IncidentSummaryDTO:
        """
        Synthesise an ``IncidentSummaryDTO`` from the orchestrator's understanding
        of the incident so far.

        Called after the first investigation pass to prepare the context for the
        diagnosis agent.
        """
        affected_components = self._graph_mapper.affected_components(graph)
        return IncidentSummaryDTO(
            investigation_id=investigation_id,
            narrative=narrative,
            affected_components=affected_components,
            trigger_type=trigger_type,
            human_context=human_context,
        )

    def build_diagnosis_request(
        self,
        *,
        summary: IncidentSummaryDTO,
        retrieved_evidence: list[RetrievedEvidence],
        events: list[CoreEvent],
        default_evidence_confidence: float = 1.0,
    ) -> DiagnosisRequest:
        """
        Build a ``DiagnosisRequest`` from an ``IncidentSummaryDTO`` and the
        results of the RAG retrieval pass.

        All live evidence is converted from ``CoreEvent`` via ``EventMapper`` so
        that the diagnosis agent never sees domain objects.
        """
        available_evidence = self._event_mapper.core_events_to_evidence(
            events, default_confidence=default_evidence_confidence
        )
        return DiagnosisRequest(
            investigation_id=summary.investigation_id,
            incident_summary=summary.narrative,
            retrieved_evidence=retrieved_evidence,
            available_evidence=available_evidence,
            trigger_type=summary.trigger_type,
            human_description=summary.human_context,
        )

    # --------------------------------------------------------------------------
    # Phase 2: ADR-008 output-boundary validation (DTO → domain)
    # --------------------------------------------------------------------------

    @staticmethod
    def validate_diagnosis_result(result: DiagnosisResult) -> None:
        """
        Enforce all ADR-008 output-boundary rules on a ``DiagnosisResult``.

        Raises
        ------
        BoundaryViolationError
            If any rule is violated.  The exception message names the broken rule.

        Rules checked
        -------------
        1. ``evidence_references`` must be non-empty.
        2. When ``remediation.requires_human_approval`` is ``True``, the
           ``remediation.risk_level`` must be ``HIGH`` or ``CRITICAL``.
        """
        # Rule 1 — evidence_references non-empty (defence-in-depth)
        if not result.evidence_references:
            raise BoundaryViolationError(
                "ADR-008 violation: DiagnosisResult.evidence_references is empty. "
                "Every claim must be backed by at least one EvidenceReference."
            )

        # Rule 2 — human-approval gate requires HIGH or CRITICAL risk level.
        remediation = result.remediation
        if (
            remediation.requires_human_approval
            and remediation.risk_level not in _HIGH_RISK_LEVELS
        ):
            raise BoundaryViolationError(
                f"ADR-008 violation: remediation.requires_human_approval is True but "
                f"risk_level is {remediation.risk_level!r}. "
                f"Must be HIGH or CRITICAL when human approval is required."
            )

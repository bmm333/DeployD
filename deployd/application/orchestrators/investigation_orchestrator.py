"""
ADR-008  (DD-17)
InvestigationOrchestrator — wires the investigation pipeline and enforces
the DTO boundary contracts.

Responsibilities
----------------
1. Accept domain objects from the use-case layer.
2. Call mappers to materialise all context into DTOs *before* the agent boundary.
3. Expose factory methods that produce ``InvestigationRequest`` and
   ``DiagnosisRequest`` — the only two types an agent may receive.
4. Validate ``DiagnosisResult`` objects coming *back* from the agent and raise
   ``BoundaryViolationError`` for any ADR-008 rule breach.

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

from deployd.application.dtos.diagnosis import DiagnosisRequest, DiagnosisResult
from deployd.application.dtos.enums import RiskLevel, TriggerType
from deployd.application.dtos.incident_summary import IncidentSummaryDTO
from deployd.application.dtos.investigation_request import InvestigationRequest
from deployd.application.dtos.retrieval import RetrievedEvidence
from deployd.application.mappers.event_mapper import EventMapper
from deployd.application.mappers.graph_mapper import GraphMapper
from deployd.domain.entities.core_event import CoreEvent
from deployd.domain.graph.graph import IncidentGraph

# Risk levels that satisfy the human-approval gate (ADR-008 §Remediation Rules)
_HIGH_RISK_LEVELS: frozenset[RiskLevel] = frozenset({RiskLevel.HIGH, RiskLevel.CRITICAL})


class BoundaryViolationError(Exception):
    """
    Raised when a ``DiagnosisResult`` violates an ADR-008 boundary contract.

    The message identifies the specific rule that was broken so that the
    calling use-case can log it, escalate, or surface it to the on-call engineer.
    """


class InvestigationOrchestrator:
    """
    Stateless pipeline orchestrator.

    Instantiate once per application startup; every public method is side-effect
    free and can be called concurrently.
    """

    def __init__(
        self,
        event_mapper: EventMapper | None = None,
        graph_mapper: GraphMapper | None = None,
    ) -> None:
        self._event_mapper = event_mapper or EventMapper()
        self._graph_mapper = graph_mapper or GraphMapper()

    # --------------------------------------------------------------------------
    # Input-boundary factories (domain → DTO)
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
    ) -> InvestigationRequest:
        """
        Materialise all context from domain objects into an ``InvestigationRequest``.

        This is the primary input-boundary crossing point.  After this call,
        the returned DTO — and only this DTO — may be handed to an AI agent.

        Parameters
        ----------
        events:
            Ordered list of ``CoreEvent`` domain objects that form the evidence
            window.  Must contain at least one event.
        graph:
            The ``IncidentGraph`` snapshot at investigation time.
        trigger_type:
            Whether the investigation was auto-detected or engineer-triggered.
        human_description:
            Optional free-text context (required when ENGINEER_TRIGGERED).
        requested_by:
            Identity of the triggering engineer, if applicable.
        timestamp:
            UTC datetime of investigation creation; defaults to now.
        """
        event_dtos = self._event_mapper.core_events_to_event_dtos(events)
        dependency_map = self._graph_mapper.graph_to_dependency_map(graph)
        affected_components = self._graph_mapper.affected_components(graph)

        return InvestigationRequest(
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

        Parameters
        ----------
        summary:
            The synthesised incident narrative produced by ``build_incident_summary``.
        retrieved_evidence:
            Ranked historical incidents from the RAG pipeline (already DTOs).
        events:
            The raw ``CoreEvent`` list to convert into ``EvidenceDTO`` items.
        default_evidence_confidence:
            Confidence value assigned to machine-generated evidence items.
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
    # Output-boundary validation (DTO → domain)
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
        # Rule 1 — evidence_references non-empty (defence-in-depth; DTO already
        # enforces min_length=1, but we re-check here to catch any future
        # relaxation of the DTO constraint).
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

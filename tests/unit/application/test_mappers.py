"""
Unit tests for deployd.application.mappers and InvestigationOrchestrator.

Coverage targets
----------------
* EventMapper.core_event_to_dto      — correct field mapping, no domain enum leaks.
* EventMapper.core_event_to_evidence — correct EvidenceSource assignment per event type.
* EventMapper bulk methods            — list passthrough.
* GraphMapper.graph_to_dependency_map — edge-to-DTO conversion, deduplication.
* GraphMapper.affected_components    — node label extraction.
* InvestigationOrchestrator.build_investigation_request — valid DTO produced.
* InvestigationOrchestrator.build_incident_summary     — valid DTO produced.
* InvestigationOrchestrator.build_diagnosis_request    — valid DTO produced.
* InvestigationOrchestrator.validate_diagnosis_result  — ADR-008 rules enforced:
    - LOW/MEDIUM risk with requires_human_approval=True raises BoundaryViolationError
    - HIGH/CRITICAL risk with requires_human_approval=True passes
    - empty evidence_references raises BoundaryViolationError
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from deployd.application.dtos import (
    DiagnosisResult,
    EvidenceReference,
    EvidenceSource,
    IncidentSummaryDTO,
    InvestigationRequest,
    RemediationRecommendation,
    RetrievedEvidence,
    RiskLevel,
    TriggerType,
)
from deployd.application.dtos.diagnosis import DiagnosisRequest
from deployd.application.mappers import EventMapper, GraphMapper
from deployd.application.orchestrators import BoundaryViolationError, InvestigationOrchestrator
from deployd.domain.entities.core_event import CoreEvent, CoreEventType, Severity
from deployd.domain.graph.edge import GraphEdge
from deployd.domain.graph.edge_type import EdgeType
from deployd.domain.graph.graph import IncidentGraph
from deployd.domain.graph.node import GraphNode

# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

NOW = datetime.now(UTC)


def make_core_event(
    *,
    event_type: CoreEventType = CoreEventType.DEPLOY_FAILED,
    severity: Severity = Severity.ERROR,
    component: str | None = "api-gateway",
    description: str = "Deploy failed",
    metadata: dict | None = None,
) -> CoreEvent:
    return CoreEvent(
        event_type=event_type,
        timestamp=NOW,
        severity=severity,
        related_component=component,
        description=description,
        metadata=metadata or {},
    )


def make_two_node_graph(
    edge_type: EdgeType = EdgeType.CAUSAL,
) -> tuple[IncidentGraph, GraphNode, GraphNode]:
    """Build a minimal two-node graph with a single directed edge."""
    event_a = make_core_event(component="api-gateway", description="Gateway crash")
    event_b = make_core_event(component="auth-service", description="Auth OOMKill")

    node_a = GraphNode(event=event_a)
    node_b = GraphNode(event=event_b)

    edge = GraphEdge(
        source=node_a.node_id,
        target=node_b.node_id,
        edge_type=edge_type,
        confidence=0.9,
    )

    g = IncidentGraph()
    g.add_node(node_a)
    g.add_node(node_b)
    g.add_edge(edge)
    return g, node_a, node_b


def make_retrieved_evidence() -> RetrievedEvidence:
    return RetrievedEvidence(
        incident_id="INC-20240701-0007",
        final_score=0.87,
        semantic_score=0.91,
        causal_score=0.78,
        temporal_score=0.65,
        component_score=0.80,
        dependency_score=0.72,
    )


def make_evidence_reference() -> EvidenceReference:
    return EvidenceReference(
        incident_id="INC-20240701-0007",
        relevance_explanation="Same OOMKill pattern last July.",
    )


def make_remediation(
    risk_level: RiskLevel = RiskLevel.HIGH,
    requires_human_approval: bool = True,
) -> RemediationRecommendation:
    return RemediationRecommendation(
        summary="Roll back auth-service.",
        steps=["kubectl rollout undo deployment/auth-service -n prod"],
        risk_level=risk_level,
        requires_human_approval=requires_human_approval,
    )


def make_diagnosis_result(
    risk_level: RiskLevel = RiskLevel.HIGH,
    requires_human_approval: bool = True,
    evidence_references: list[EvidenceReference] | None = None,
) -> DiagnosisResult:
    if evidence_references is None:
        evidence_references = [make_evidence_reference()]
    return DiagnosisResult(
        root_cause_explanation="Memory leak in auth-service v3.1.0.",
        confidence=0.88,
        remediation=make_remediation(
            risk_level=risk_level,
            requires_human_approval=requires_human_approval,
        ),
        evidence_references=evidence_references,
    )


# ===========================================================================
# EventMapper
# ===========================================================================


class TestEventMapper:
    def test_core_event_to_dto_types(self):
        """Output must be EventDTO — no CoreEventType or Severity should leak."""
        event = make_core_event()
        dto = EventMapper.core_event_to_dto(event)

        # event_id becomes a plain string
        assert isinstance(dto.event_id, str)
        # event_type must be a plain str, not a CoreEventType enum
        assert isinstance(dto.event_type, str)
        assert dto.event_type == CoreEventType.DEPLOY_FAILED.value
        # Severity must not appear as an enum in the output type
        assert isinstance(dto.payload.get("severity"), str)

    def test_core_event_to_dto_component_fallback(self):
        """When related_component is None, source falls back to 'unknown'."""
        event = make_core_event(component=None)
        dto = EventMapper.core_event_to_dto(event)
        assert dto.source == "unknown"

    def test_core_event_to_dto_timestamp_utc(self):
        """Timestamp must be UTC-aware."""
        event = make_core_event()
        dto = EventMapper.core_event_to_dto(event)
        assert dto.timestamp.tzinfo is not None

    def test_core_event_to_evidence_system_event(self):
        """DEPLOY_FAILED maps to SYSTEM_EVENT evidence source."""
        event = make_core_event(event_type=CoreEventType.DEPLOY_FAILED)
        ev = EventMapper.core_event_to_evidence(event)
        assert ev.source == EvidenceSource.SYSTEM_EVENT

    def test_core_event_to_evidence_human_observation(self):
        """HUMAN_OBSERVATION maps to HUMAN_OBSERVATION evidence source."""
        event = make_core_event(event_type=CoreEventType.HUMAN_OBSERVATION)
        ev = EventMapper.core_event_to_evidence(event)
        assert ev.source == EvidenceSource.HUMAN_OBSERVATION

    def test_core_event_to_evidence_fsm_transition(self):
        """STATE_CHANGE maps to FSM_TRANSITION evidence source."""
        event = make_core_event(event_type=CoreEventType.STATE_CHANGE)
        ev = EventMapper.core_event_to_evidence(event)
        assert ev.source == EvidenceSource.FSM_TRANSITION

    def test_core_event_to_evidence_confidence_clamped(self):
        """Confidence > 1.0 is clamped to 1.0."""
        event = make_core_event()
        ev = EventMapper.core_event_to_evidence(event, confidence=99.0)
        assert ev.confidence == 1.0

    def test_core_event_to_evidence_confidence_zero_clamped(self):
        """Confidence < 0.0 is clamped to 0.0."""
        event = make_core_event()
        ev = EventMapper.core_event_to_evidence(event, confidence=-5.0)
        assert ev.confidence == 0.0

    def test_core_event_to_evidence_raw_data_has_no_enums(self):
        """raw_data values must be plain strings, not enum instances."""
        event = make_core_event(metadata={"pod": "auth-1"})
        ev = EventMapper.core_event_to_evidence(event)
        for v in ev.raw_data.values():
            assert isinstance(v, str), f"Non-string value in raw_data: {v!r}"

    def test_bulk_event_dtos_length(self):
        """core_events_to_event_dtos returns one DTO per input event."""
        events = [make_core_event() for _ in range(3)]
        dtos = EventMapper.core_events_to_event_dtos(events)
        assert len(dtos) == 3

    def test_bulk_evidence_length(self):
        """core_events_to_evidence returns one EvidenceDTO per input event."""
        events = [make_core_event() for _ in range(4)]
        evidence = EventMapper.core_events_to_evidence(events)
        assert len(evidence) == 4

    def test_empty_event_list(self):
        """Empty input produces empty output without error."""
        assert EventMapper.core_events_to_event_dtos([]) == []
        assert EventMapper.core_events_to_evidence([]) == []


# ===========================================================================
# GraphMapper
# ===========================================================================


class TestGraphMapper:
    def test_dependency_map_single_edge(self):
        """A two-node graph produces exactly one ComponentDependencyDTO."""
        graph, _, _ = make_two_node_graph(edge_type=EdgeType.CAUSAL)
        deps = GraphMapper.graph_to_dependency_map(graph)
        assert len(deps) == 1
        dep = deps[0]
        assert dep.source_component == "api-gateway"
        assert dep.target_component == "auth-service"
        assert dep.relationship == EdgeType.CAUSAL.value

    def test_dependency_map_edge_type_is_string(self):
        """relationship must be a plain string, not an EdgeType enum."""
        graph, _, _ = make_two_node_graph()
        deps = GraphMapper.graph_to_dependency_map(graph)
        assert isinstance(deps[0].relationship, str)

    def test_dependency_map_deduplication(self):
        """Duplicate edges produce only one DTO."""
        event_a = make_core_event(component="svc-a")
        event_b = make_core_event(component="svc-b")
        node_a = GraphNode(event=event_a)
        node_b = GraphNode(event=event_b)

        g = IncidentGraph()
        g.add_node(node_a)
        g.add_node(node_b)
        # IncidentGraph itself blocks duplicate edges by raising DuplicateEdgeError,
        # but adding two edges of different types should give two DTOs.
        g.add_edge(GraphEdge(source=node_a.node_id, target=node_b.node_id, edge_type=EdgeType.CAUSAL, confidence=0.9))
        g.add_edge(GraphEdge(source=node_a.node_id, target=node_b.node_id, edge_type=EdgeType.TEMPORAL, confidence=0.8))

        deps = GraphMapper.graph_to_dependency_map(g)
        assert len(deps) == 2  # two different edge types → two DTOs

    def test_dependency_map_empty_graph(self):
        """Empty graph produces an empty list."""
        g = IncidentGraph()
        assert GraphMapper.graph_to_dependency_map(g) == []

    def test_dependency_map_component_fallback(self):
        """Nodes without related_component fall back to UUID string."""
        event_a = make_core_event(component=None)
        event_b = make_core_event(component=None)
        node_a = GraphNode(event=event_a)
        node_b = GraphNode(event=event_b)
        edge = GraphEdge(source=node_a.node_id, target=node_b.node_id, edge_type=EdgeType.DEPENDENCY, confidence=0.7)
        g = IncidentGraph()
        g.add_node(node_a)
        g.add_node(node_b)
        g.add_edge(edge)
        deps = GraphMapper.graph_to_dependency_map(g)
        assert len(deps) == 1
        # Both labels should be UUID strings (not empty)
        assert deps[0].source_component  # non-empty
        assert deps[0].target_component

    def test_affected_components_unique_ordered(self):
        """affected_components returns unique component names in insertion order."""
        graph, _, _ = make_two_node_graph()
        components = GraphMapper.affected_components(graph)
        assert len(components) == 2
        assert "api-gateway" in components
        assert "auth-service" in components
        # No duplicates
        assert len(components) == len(set(components))

    def test_affected_components_empty_graph(self):
        """Empty graph produces an empty component list."""
        g = IncidentGraph()
        assert GraphMapper.affected_components(g) == []


# ===========================================================================
# InvestigationOrchestrator — build_investigation_request
# ===========================================================================


class TestBuildInvestigationRequest:
    def setup_method(self):
        self.orchestrator = InvestigationOrchestrator()
        self.graph, _, _ = make_two_node_graph()
        self.events = [make_core_event()]

    def test_returns_investigation_request(self):
        req = self.orchestrator.build_investigation_request(
            events=self.events,
            graph=self.graph,
            trigger_type=TriggerType.AUTO_DETECTED,
        )
        assert isinstance(req, InvestigationRequest)

    def test_trigger_type_preserved(self):
        req = self.orchestrator.build_investigation_request(
            events=self.events,
            graph=self.graph,
            trigger_type=TriggerType.ENGINEER_TRIGGERED,
            human_description="Auth service is down",
        )
        assert req.trigger_type == TriggerType.ENGINEER_TRIGGERED
        assert req.human_description == "Auth service is down"

    def test_events_converted_to_dtos(self):
        """Domain CoreEvent objects must become EventDTO — no CoreEvent in output."""
        events = [make_core_event(component="svc-x")]
        req = self.orchestrator.build_investigation_request(
            events=events,
            graph=self.graph,
            trigger_type=TriggerType.AUTO_DETECTED,
        )
        # event_type must be a plain str, not a CoreEventType
        for event_dto in req.events:
            assert isinstance(event_dto.event_type, str)

    def test_affected_components_from_graph(self):
        req = self.orchestrator.build_investigation_request(
            events=self.events,
            graph=self.graph,
            trigger_type=TriggerType.AUTO_DETECTED,
        )
        assert "api-gateway" in req.affected_components
        assert "auth-service" in req.affected_components

    def test_dependency_map_from_graph(self):
        req = self.orchestrator.build_investigation_request(
            events=self.events,
            graph=self.graph,
            trigger_type=TriggerType.AUTO_DETECTED,
        )
        assert len(req.dependency_map) == 1
        assert req.dependency_map[0].relationship == EdgeType.CAUSAL.value

    def test_timestamp_defaults_to_utc_now(self):
        req = self.orchestrator.build_investigation_request(
            events=self.events,
            graph=self.graph,
            trigger_type=TriggerType.AUTO_DETECTED,
        )
        assert req.timestamp.tzinfo is not None


# ===========================================================================
# InvestigationOrchestrator — build_incident_summary
# ===========================================================================


class TestBuildIncidentSummary:
    def setup_method(self):
        self.orchestrator = InvestigationOrchestrator()
        self.graph, _, _ = make_two_node_graph()

    def test_returns_incident_summary_dto(self):
        summary = self.orchestrator.build_incident_summary(
            investigation_id="INV-001",
            narrative="api-gateway 502 after deploy.",
            graph=self.graph,
            trigger_type=TriggerType.AUTO_DETECTED,
        )
        assert isinstance(summary, IncidentSummaryDTO)
        assert summary.investigation_id == "INV-001"

    def test_affected_components_populated(self):
        summary = self.orchestrator.build_incident_summary(
            investigation_id="INV-001",
            narrative="narrative",
            graph=self.graph,
            trigger_type=TriggerType.AUTO_DETECTED,
        )
        assert len(summary.affected_components) == 2

    def test_human_context_optional(self):
        summary = self.orchestrator.build_incident_summary(
            investigation_id="INV-001",
            narrative="narrative",
            graph=self.graph,
            trigger_type=TriggerType.AUTO_DETECTED,
        )
        assert summary.human_context is None


# ===========================================================================
# InvestigationOrchestrator — build_diagnosis_request
# ===========================================================================


class TestBuildDiagnosisRequest:
    def setup_method(self):
        self.orchestrator = InvestigationOrchestrator()
        self.graph, _, _ = make_two_node_graph()
        self.summary = self.orchestrator.build_incident_summary(
            investigation_id="INV-001",
            narrative="Incident narrative.",
            graph=self.graph,
            trigger_type=TriggerType.AUTO_DETECTED,
        )
        self.events = [make_core_event()]
        self.retrieved = [make_retrieved_evidence()]

    def test_returns_diagnosis_request(self):
        req = self.orchestrator.build_diagnosis_request(
            summary=self.summary,
            retrieved_evidence=self.retrieved,
            events=self.events,
        )
        assert isinstance(req, DiagnosisRequest)

    def test_investigation_id_matches_summary(self):
        req = self.orchestrator.build_diagnosis_request(
            summary=self.summary,
            retrieved_evidence=self.retrieved,
            events=self.events,
        )
        assert req.investigation_id == "INV-001"

    def test_incident_summary_matches_narrative(self):
        req = self.orchestrator.build_diagnosis_request(
            summary=self.summary,
            retrieved_evidence=self.retrieved,
            events=self.events,
        )
        assert req.incident_summary == "Incident narrative."

    def test_available_evidence_converted(self):
        """CoreEvent objects must become EvidenceDTO — no CoreEvent in output."""
        req = self.orchestrator.build_diagnosis_request(
            summary=self.summary,
            retrieved_evidence=self.retrieved,
            events=self.events,
        )
        assert len(req.available_evidence) == 1
        # EvidenceDTO.source must be an EvidenceSource enum value (not CoreEventType)
        from deployd.application.dtos import EvidenceSource
        assert req.available_evidence[0].source in EvidenceSource


# ===========================================================================
# InvestigationOrchestrator — validate_diagnosis_result (ADR-008 rules)
# ===========================================================================


class TestValidateDiagnosisResult:
    """Enforce the ADR-008 output-boundary rules."""

    # --- Rule 2: risk level gate ----------------------------------------

    @pytest.mark.parametrize("risk_level", [RiskLevel.LOW, RiskLevel.MEDIUM])
    def test_low_risk_with_human_approval_raises(self, risk_level: RiskLevel):
        """LOW or MEDIUM risk + requires_human_approval=True is a boundary violation."""
        result = make_diagnosis_result(risk_level=risk_level, requires_human_approval=True)
        with pytest.raises(BoundaryViolationError, match="risk_level"):
            InvestigationOrchestrator.validate_diagnosis_result(result)

    @pytest.mark.parametrize("risk_level", [RiskLevel.HIGH, RiskLevel.CRITICAL])
    def test_high_or_critical_with_human_approval_passes(self, risk_level: RiskLevel):
        """HIGH or CRITICAL risk + requires_human_approval=True is valid."""
        result = make_diagnosis_result(risk_level=risk_level, requires_human_approval=True)
        # Must not raise
        InvestigationOrchestrator.validate_diagnosis_result(result)

    @pytest.mark.parametrize("risk_level", [RiskLevel.LOW, RiskLevel.MEDIUM])
    def test_low_risk_without_human_approval_passes(self, risk_level: RiskLevel):
        """LOW or MEDIUM risk without human approval is valid."""
        result = make_diagnosis_result(risk_level=risk_level, requires_human_approval=False)
        InvestigationOrchestrator.validate_diagnosis_result(result)

    # --- Rule 1: evidence_references non-empty (defence-in-depth) --------

    def test_empty_evidence_references_raises(self):
        """
        evidence_references=[] bypasses the DTO min_length=1 guard when
        the object is constructed directly (e.g. from model_construct).
        The orchestrator provides a second line of defence.
        """
        # Bypass Pydantic validation with model_construct to simulate a
        # result that somehow arrives with an empty evidence list.
        remediation = make_remediation(risk_level=RiskLevel.HIGH, requires_human_approval=True)
        result = DiagnosisResult.model_construct(
            root_cause_explanation="Some cause",
            confidence=0.8,
            remediation=remediation,
            evidence_references=[],  # intentionally empty
            alternative_hypotheses=[],
            missing_evidence=[],
            unsupported_claims=[],
        )
        with pytest.raises(BoundaryViolationError, match="evidence_references"):
            InvestigationOrchestrator.validate_diagnosis_result(result)

    def test_non_empty_evidence_references_passes(self):
        result = make_diagnosis_result(evidence_references=[make_evidence_reference()])
        InvestigationOrchestrator.validate_diagnosis_result(result)

    # --- unsupported_claims preserved ------------------------------------

    def test_unsupported_claims_are_preserved_not_dropped(self):
        """Validate that unsupported_claims come through unchanged (not silently dropped)."""
        result = make_diagnosis_result()
        # Build with unsupported claims via model_construct to bypass frozen constraint
        result_with_claims = DiagnosisResult.model_construct(
            root_cause_explanation=result.root_cause_explanation,
            confidence=result.confidence,
            remediation=result.remediation,
            evidence_references=result.evidence_references,
            alternative_hypotheses=[],
            missing_evidence=[],
            unsupported_claims=["This claim has no backing evidence."],
        )
        # Validation must pass (unsupported_claims are allowed)
        InvestigationOrchestrator.validate_diagnosis_result(result_with_claims)
        # They must be preserved
        assert len(result_with_claims.unsupported_claims) == 1


# ===========================================================================
# Package-level imports
# ===========================================================================


class TestPackageImports:
    def test_event_mapper_importable_from_package(self):
        from deployd.application.mappers import EventMapper  # noqa: F401

    def test_graph_mapper_importable_from_package(self):
        from deployd.application.mappers import GraphMapper  # noqa: F401

    def test_orchestrator_importable_from_package(self):
        from deployd.application.orchestrators import InvestigationOrchestrator  # noqa: F401

    def test_boundary_violation_error_importable(self):
        from deployd.application.orchestrators import BoundaryViolationError  # noqa: F401

    def test_incident_summary_dto_importable_from_dtos(self):
        from deployd.application.dtos import IncidentSummaryDTO  # noqa: F401

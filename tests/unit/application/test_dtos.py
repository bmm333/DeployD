"""
Unit tests for deployd.application.dtos

Coverage targets
----------------
* All DTO models can be constructed with valid data.
* Optional fields default correctly.
* Required fields raise ``ValidationError`` when missing.
* Enum fields reject invalid string literals.
* All ``confidence`` / ``*_score`` floats are constrained to [0.0, 1.0].
* ``min_length`` constraints on string and list fields are respected.
* All models are frozen (immutable after construction).
* The public ``__init__`` re-exports every symbol.
* JSON round-trip is lossless.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from deployd.application.dtos import (
    AlternativeHypothesis,
    ComponentDependencyDTO,
    DiagnosisRequest,
    DiagnosisResult,
    EvidenceDTO,
    EvidenceReference,
    EvidenceSource,
    InvestigationRequest,
    MissingEvidence,
    RemediationRecommendation,
    RetrievedEvidence,
    RiskLevel,
    TriggerType,
)
from deployd.application.dtos import EventDTO  # also via __init__


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

NOW = datetime.now(UTC)


def make_event_dto(**overrides):
    defaults = {
        "event_id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
        "timestamp": NOW,
        "event_type": "DEPLOY_FAILED",
        "source": "kubernetes-prod",
        "payload": {"exit_code": 1},
    }
    return EventDTO(**(defaults | overrides))


def make_component_dep(**overrides):
    defaults = {
        "source_component": "api-gateway",
        "target_component": "auth-service",
        "relationship": "HTTP",
    }
    return ComponentDependencyDTO(**(defaults | overrides))


def make_evidence_dto(**overrides):
    defaults = {
        "evidence_id": "7c9e6679-7425-40de-944b-e07fc1f90ae7",
        "source": EvidenceSource.SYSTEM_EVENT,
        "description": "CPU spiked to 98%",
        "component": "api-gateway",
        "timestamp": NOW,
        "confidence": 0.9,
    }
    return EvidenceDTO(**(defaults | overrides))


def make_retrieved_evidence(**overrides):
    defaults = {
        "incident_id": "INC-20240701-0007",
        "final_score": 0.87,
        "semantic_score": 0.91,
        "causal_score": 0.78,
        "temporal_score": 0.65,
        "component_score": 0.80,
        "dependency_score": 0.72,
    }
    return RetrievedEvidence(**(defaults | overrides))


def make_evidence_reference(**overrides):
    defaults = {
        "incident_id": "INC-20240701-0007",
        "relevance_explanation": "Same OOMKill pattern last July.",
        "similarity_scores": {"semantic": 0.91},
    }
    return EvidenceReference(**(defaults | overrides))


def make_remediation(**overrides):
    defaults = {
        "summary": "Roll back auth-service.",
        "steps": ["kubectl rollout undo deployment/auth-service -n prod"],
        "risk_level": RiskLevel.HIGH,
        "requires_human_approval": True,
    }
    return RemediationRecommendation(**(defaults | overrides))


def make_diagnosis_result(**overrides):
    defaults = {
        "root_cause_explanation": "Memory leak in auth-service v3.1.0.",
        "confidence": 0.88,
        "remediation": make_remediation(),
        "evidence_references": [make_evidence_reference()],
    }
    return DiagnosisResult(**(defaults | overrides))


# ===========================================================================
# Enums
# ===========================================================================


class TestEnums:
    def test_trigger_type_values(self):
        assert TriggerType.AUTO_DETECTED == "AUTO_DETECTED"
        assert TriggerType.ENGINEER_TRIGGERED == "ENGINEER_TRIGGERED"

    def test_evidence_source_values(self):
        assert EvidenceSource.SYSTEM_EVENT == "SYSTEM_EVENT"
        assert EvidenceSource.HUMAN_OBSERVATION == "HUMAN_OBSERVATION"
        assert EvidenceSource.FSM_TRANSITION == "FSM_TRANSITION"

    def test_risk_level_values(self):
        assert RiskLevel.LOW == "LOW"
        assert RiskLevel.MEDIUM == "MEDIUM"
        assert RiskLevel.HIGH == "HIGH"
        assert RiskLevel.CRITICAL == "CRITICAL"

    def test_trigger_type_is_str(self):
        assert isinstance(TriggerType.AUTO_DETECTED, str)

    def test_risk_level_is_str(self):
        assert isinstance(RiskLevel.CRITICAL, str)


# ===========================================================================
# EventDTO
# ===========================================================================


class TestEventDTO:
    def test_valid_construction(self):
        dto = make_event_dto()
        assert dto.event_id == "3fa85f64-5717-4562-b3fc-2c963f66afa6"
        assert dto.event_type == "DEPLOY_FAILED"

    def test_payload_defaults_to_empty_dict(self):
        # payload has default_factory=dict — omitting it yields an empty dict.
        dto = EventDTO(
            event_id="abc",
            timestamp=NOW,
            event_type="X",
            source="src",
        )
        assert dto.payload == {}

    def test_missing_event_id_raises(self):
        with pytest.raises(ValidationError):
            EventDTO(timestamp=NOW, event_type="X", source="src")

    def test_missing_timestamp_raises(self):
        with pytest.raises(ValidationError):
            EventDTO(event_id="abc", event_type="X", source="src")

    def test_empty_event_type_raises(self):
        with pytest.raises(ValidationError):
            make_event_dto(event_type="")

    def test_empty_source_raises(self):
        with pytest.raises(ValidationError):
            make_event_dto(source="")

    def test_frozen(self):
        dto = make_event_dto()
        with pytest.raises(ValidationError):
            dto.event_type = "SOMETHING_ELSE"  # type: ignore[misc]

    def test_json_round_trip(self):
        dto = make_event_dto()
        data = json.loads(dto.model_dump_json())
        restored = EventDTO.model_validate(data)
        assert restored.event_id == dto.event_id


# ===========================================================================
# ComponentDependencyDTO
# ===========================================================================


class TestComponentDependencyDTO:
    def test_valid_construction(self):
        dto = make_component_dep()
        assert dto.source_component == "api-gateway"
        assert dto.relationship == "HTTP"

    def test_empty_source_component_raises(self):
        with pytest.raises(ValidationError):
            make_component_dep(source_component="")

    def test_empty_target_component_raises(self):
        with pytest.raises(ValidationError):
            make_component_dep(target_component="")

    def test_empty_relationship_raises(self):
        with pytest.raises(ValidationError):
            make_component_dep(relationship="")

    def test_frozen(self):
        dto = make_component_dep()
        with pytest.raises(ValidationError):
            dto.relationship = "gRPC"  # type: ignore[misc]


# ===========================================================================
# InvestigationRequest
# ===========================================================================


class TestInvestigationRequest:
    def _make(self, **overrides):
        defaults = {
            "trigger_type": TriggerType.AUTO_DETECTED,
            "events": [make_event_dto()],
            "affected_components": ["api-gateway"],
            "timestamp": NOW,
        }
        return InvestigationRequest(**(defaults | overrides))

    def test_valid_auto_detected(self):
        req = self._make()
        assert req.trigger_type == TriggerType.AUTO_DETECTED
        assert req.human_description is None
        assert req.requested_by is None

    def test_valid_engineer_triggered(self):
        req = self._make(
            trigger_type=TriggerType.ENGINEER_TRIGGERED,
            human_description="auth-service down",
            requested_by="alice@example.com",
        )
        assert req.trigger_type == TriggerType.ENGINEER_TRIGGERED
        assert req.human_description == "auth-service down"

    def test_dependency_map_defaults_to_empty(self):
        req = self._make()
        assert req.dependency_map == []

    def test_missing_events_raises(self):
        with pytest.raises(ValidationError):
            self._make(events=[])  # min_length=1

    def test_missing_trigger_type_raises(self):
        with pytest.raises(ValidationError):
            InvestigationRequest(
                events=[make_event_dto()],
                affected_components=["x"],
                timestamp=NOW,
            )

    def test_invalid_trigger_type_raises(self):
        with pytest.raises(ValidationError):
            self._make(trigger_type="NOT_A_VALID_VALUE")

    def test_frozen(self):
        req = self._make()
        with pytest.raises(ValidationError):
            req.trigger_type = TriggerType.ENGINEER_TRIGGERED  # type: ignore[misc]

    def test_json_serialisable(self):
        req = self._make(dependency_map=[make_component_dep()])
        payload = req.model_dump_json()
        assert json.loads(payload)  # must not raise


# ===========================================================================
# EvidenceDTO
# ===========================================================================


class TestEvidenceDTO:
    def test_valid_construction(self):
        e = make_evidence_dto()
        assert e.confidence == 0.9
        assert e.source == EvidenceSource.SYSTEM_EVENT

    def test_boundary_confidence_zero(self):
        e = make_evidence_dto(confidence=0.0)
        assert e.confidence == 0.0

    def test_boundary_confidence_one(self):
        e = make_evidence_dto(confidence=1.0)
        assert e.confidence == 1.0

    def test_confidence_above_one_raises(self):
        with pytest.raises(ValidationError):
            make_evidence_dto(confidence=1.01)

    def test_confidence_below_zero_raises(self):
        with pytest.raises(ValidationError):
            make_evidence_dto(confidence=-0.01)

    def test_invalid_source_raises(self):
        with pytest.raises(ValidationError):
            make_evidence_dto(source="ALIEN_SIGNAL")

    def test_empty_description_raises(self):
        with pytest.raises(ValidationError):
            make_evidence_dto(description="")

    def test_empty_component_raises(self):
        with pytest.raises(ValidationError):
            make_evidence_dto(component="")

    def test_raw_data_defaults_to_empty_dict(self):
        e = EvidenceDTO(
            evidence_id="x",
            source=EvidenceSource.FSM_TRANSITION,
            description="FSM moved to FAILED",
            component="fsm",
            timestamp=NOW,
            confidence=0.5,
        )
        assert e.raw_data == {}

    def test_frozen(self):
        e = make_evidence_dto()
        with pytest.raises(ValidationError):
            e.confidence = 0.1  # type: ignore[misc]


# ===========================================================================
# MissingEvidence
# ===========================================================================


class TestMissingEvidence:
    def _make(self, **overrides):
        defaults = {
            "description": "Pod restart logs missing",
            "why_needed": "Needed to distinguish OOMKill from probe failure",
            "collection_method": "kubectl logs -n prod auth-service --since=1h",
        }
        return MissingEvidence(**(defaults | overrides))

    def test_valid_construction(self):
        m = self._make()
        assert m.source_incident_id is None

    def test_with_source_incident_id(self):
        m = self._make(source_incident_id="INC-20240815-0042")
        assert m.source_incident_id == "INC-20240815-0042"

    def test_empty_description_raises(self):
        with pytest.raises(ValidationError):
            self._make(description="")

    def test_empty_why_needed_raises(self):
        with pytest.raises(ValidationError):
            self._make(why_needed="")

    def test_empty_collection_method_raises(self):
        with pytest.raises(ValidationError):
            self._make(collection_method="")

    def test_frozen(self):
        m = self._make()
        with pytest.raises(ValidationError):
            m.description = "new"  # type: ignore[misc]


# ===========================================================================
# RetrievedEvidence
# ===========================================================================


class TestRetrievedEvidence:
    def test_valid_construction(self):
        r = make_retrieved_evidence()
        assert r.incident_id == "INC-20240701-0007"
        assert r.final_score == 0.87

    @pytest.mark.parametrize(
        "field",
        [
            "final_score",
            "semantic_score",
            "causal_score",
            "temporal_score",
            "component_score",
            "dependency_score",
        ],
    )
    def test_score_above_one_raises(self, field):
        with pytest.raises(ValidationError):
            make_retrieved_evidence(**{field: 1.1})

    @pytest.mark.parametrize(
        "field",
        [
            "final_score",
            "semantic_score",
            "causal_score",
            "temporal_score",
            "component_score",
            "dependency_score",
        ],
    )
    def test_score_below_zero_raises(self, field):
        with pytest.raises(ValidationError):
            make_retrieved_evidence(**{field: -0.1})

    def test_optional_fields_default_none(self):
        r = make_retrieved_evidence()
        assert r.historical_root_cause is None
        assert r.historical_fix is None
        assert r.runbook_id is None

    def test_list_fields_default_empty(self):
        r = make_retrieved_evidence()
        assert r.matched_causal_chain == []
        assert r.matched_nodes == []
        assert r.provenance == {}

    def test_boundary_scores_at_zero_and_one(self):
        r = make_retrieved_evidence(
            final_score=0.0,
            semantic_score=1.0,
            causal_score=0.0,
            temporal_score=1.0,
            component_score=0.0,
            dependency_score=1.0,
        )
        assert r.final_score == 0.0
        assert r.semantic_score == 1.0

    def test_frozen(self):
        r = make_retrieved_evidence()
        with pytest.raises(ValidationError):
            r.final_score = 0.5  # type: ignore[misc]

    def test_json_round_trip(self):
        r = make_retrieved_evidence(
            matched_causal_chain=["DEPLOY_FAILED", "PROCESS_CRASH"],
            historical_root_cause="OOMKill",
        )
        data = json.loads(r.model_dump_json())
        restored = RetrievedEvidence.model_validate(data)
        assert restored.historical_root_cause == "OOMKill"


# ===========================================================================
# EvidenceReference
# ===========================================================================


class TestEvidenceReference:
    def test_valid_construction(self):
        ref = make_evidence_reference()
        assert ref.incident_id == "INC-20240701-0007"

    def test_similarity_scores_defaults_empty(self):
        ref = EvidenceReference(
            incident_id="INC-001",
            relevance_explanation="Relevant",
        )
        assert ref.similarity_scores == {}

    def test_empty_relevance_explanation_raises(self):
        with pytest.raises(ValidationError):
            EvidenceReference(incident_id="INC-001", relevance_explanation="")

    def test_missing_incident_id_raises(self):
        with pytest.raises(ValidationError):
            EvidenceReference(relevance_explanation="Relevant")

    def test_frozen(self):
        ref = make_evidence_reference()
        with pytest.raises(ValidationError):
            ref.incident_id = "INC-999"  # type: ignore[misc]


# ===========================================================================
# RemediationRecommendation
# ===========================================================================


class TestRemediationRecommendation:
    def test_valid_construction(self):
        r = make_remediation()
        assert r.risk_level == RiskLevel.HIGH
        assert r.requires_human_approval is True

    def test_all_risk_levels_accepted(self):
        for level in RiskLevel:
            r = make_remediation(risk_level=level)
            assert r.risk_level == level

    def test_empty_summary_raises(self):
        with pytest.raises(ValidationError):
            make_remediation(summary="")

    def test_empty_steps_raises(self):
        with pytest.raises(ValidationError):
            make_remediation(steps=[])

    def test_invalid_risk_level_raises(self):
        with pytest.raises(ValidationError):
            make_remediation(risk_level="EXTREME")

    def test_prerequisites_defaults_empty(self):
        r = make_remediation()
        assert r.prerequisites == []

    def test_frozen(self):
        r = make_remediation()
        with pytest.raises(ValidationError):
            r.risk_level = RiskLevel.LOW  # type: ignore[misc]


# ===========================================================================
# AlternativeHypothesis
# ===========================================================================


class TestAlternativeHypothesis:
    def _make(self, **overrides):
        defaults = {
            "explanation": "Network partition caused cascading timeouts.",
            "confidence": 0.35,
        }
        return AlternativeHypothesis(**(defaults | overrides))

    def test_valid_construction(self):
        a = self._make()
        assert a.confidence == 0.35

    def test_confidence_above_one_raises(self):
        with pytest.raises(ValidationError):
            self._make(confidence=1.5)

    def test_confidence_below_zero_raises(self):
        with pytest.raises(ValidationError):
            self._make(confidence=-0.1)

    def test_empty_explanation_raises(self):
        with pytest.raises(ValidationError):
            self._make(explanation="")

    def test_list_defaults_empty(self):
        a = self._make()
        assert a.supporting_evidence == []
        assert a.missing_evidence == []

    def test_frozen(self):
        a = self._make()
        with pytest.raises(ValidationError):
            a.confidence = 0.9  # type: ignore[misc]


# ===========================================================================
# DiagnosisRequest
# ===========================================================================


class TestDiagnosisRequest:
    def _make(self, **overrides):
        defaults = {
            "investigation_id": "INV-20260816-0001",
            "incident_summary": "api-gateway 502 after deploy.",
            "retrieved_evidence": [make_retrieved_evidence()],
            "available_evidence": [make_evidence_dto()],
            "trigger_type": TriggerType.AUTO_DETECTED,
        }
        return DiagnosisRequest(**(defaults | overrides))

    def test_valid_construction(self):
        req = self._make()
        assert req.investigation_id == "INV-20260816-0001"
        assert req.human_description is None

    def test_engineer_triggered_with_description(self):
        req = self._make(
            trigger_type=TriggerType.ENGINEER_TRIGGERED,
            human_description="Database went down",
        )
        assert req.human_description == "Database went down"

    def test_missing_investigation_id_raises(self):
        with pytest.raises(ValidationError):
            DiagnosisRequest(
                incident_summary="x",
                retrieved_evidence=[],
                available_evidence=[],
                trigger_type=TriggerType.AUTO_DETECTED,
            )

    def test_empty_incident_summary_raises(self):
        with pytest.raises(ValidationError):
            self._make(incident_summary="")

    def test_invalid_trigger_type_raises(self):
        with pytest.raises(ValidationError):
            self._make(trigger_type="UNKNOWN")

    def test_frozen(self):
        req = self._make()
        with pytest.raises(ValidationError):
            req.investigation_id = "NEW"  # type: ignore[misc]

    def test_json_serialisable(self):
        req = self._make()
        payload = req.model_dump_json()
        assert json.loads(payload)


# ===========================================================================
# DiagnosisResult
# ===========================================================================


class TestDiagnosisResult:
    def test_valid_construction(self):
        result = make_diagnosis_result()
        assert result.confidence == 0.88
        assert len(result.evidence_references) == 1

    def test_confidence_boundary_values(self):
        make_diagnosis_result(confidence=0.0)
        make_diagnosis_result(confidence=1.0)

    def test_confidence_above_one_raises(self):
        with pytest.raises(ValidationError):
            make_diagnosis_result(confidence=1.01)

    def test_confidence_below_zero_raises(self):
        with pytest.raises(ValidationError):
            make_diagnosis_result(confidence=-0.01)

    def test_empty_root_cause_explanation_raises(self):
        with pytest.raises(ValidationError):
            make_diagnosis_result(root_cause_explanation="")

    def test_empty_evidence_references_raises(self):
        with pytest.raises(ValidationError):
            make_diagnosis_result(evidence_references=[])  # min_length=1

    def test_optional_lists_default_empty(self):
        result = make_diagnosis_result()
        assert result.alternative_hypotheses == []
        assert result.missing_evidence == []
        assert result.unsupported_claims == []

    def test_with_alternative_hypotheses(self):
        alt = AlternativeHypothesis(
            explanation="Network partition.",
            confidence=0.25,
        )
        result = make_diagnosis_result(alternative_hypotheses=[alt])
        assert len(result.alternative_hypotheses) == 1

    def test_with_missing_evidence(self):
        gap = MissingEvidence(
            description="Pod logs",
            why_needed="Distinguish OOMKill",
            collection_method="kubectl logs",
        )
        result = make_diagnosis_result(missing_evidence=[gap])
        assert len(result.missing_evidence) == 1

    def test_frozen(self):
        result = make_diagnosis_result()
        with pytest.raises(ValidationError):
            result.confidence = 0.5  # type: ignore[misc]

    def test_json_round_trip(self):
        result = make_diagnosis_result()
        data = json.loads(result.model_dump_json())
        restored = DiagnosisResult.model_validate(data)
        assert restored.root_cause_explanation == result.root_cause_explanation
        assert restored.confidence == result.confidence


# ===========================================================================
# Public __init__ exports
# ===========================================================================


class TestPublicExports:
    """Verify that every symbol documented in __all__ is importable from the package root."""

    def test_all_symbols_importable(self):
        import deployd.application.dtos as dtos_pkg

        for name in dtos_pkg.__all__:
            assert hasattr(dtos_pkg, name), f"Missing export: {name}"

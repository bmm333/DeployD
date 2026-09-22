import pytest
from deployd.application.dtos.diagnosis import (
    AlternativeHypothesis,
    DiagnosisRequest,
    DiagnosisResult,
    RemediationRecommendation,
)
from deployd.application.dtos.enums import RiskLevel, TriggerType
from deployd.application.dtos.evidence import MissingEvidence
from deployd.application.dtos.incident_summary import IncidentSummaryDTO
from deployd.application.dtos.retrieval import EvidenceReference
from pydantic import ValidationError


def test_alternative_hypothesis_valid():
    ref = EvidenceReference(
        incident_id="INC-123",
        relevance_explanation="Related to OOM",
        similarity_scores={"semantic": 0.9},
    )
    missing = MissingEvidence(
        description="Missing logs",
        why_needed="To confirm memory usage",
        collection_method="Check datadog",
    )
    hypo = AlternativeHypothesis(
        explanation="Could be a memory leak",
        confidence=0.8,
        supporting_evidence=[ref],
        missing_evidence=[missing],
    )
    assert hypo.explanation == "Could be a memory leak"
    assert hypo.confidence == 0.8
    assert len(hypo.supporting_evidence) == 1
    assert len(hypo.missing_evidence) == 1
    # Check JSON serializable
    assert hypo.model_dump_json()


def test_alternative_hypothesis_invalid_confidence():
    with pytest.raises(ValidationError):
        AlternativeHypothesis(
            explanation="Invalid confidence",
            confidence=1.5,
            supporting_evidence=[],
            missing_evidence=[],
        )


def test_remediation_recommendation_valid():
    rec = RemediationRecommendation(
        summary="Restart the pods",
        steps=["kubectl delete pods"],
        risk_level=RiskLevel.MEDIUM,
        prerequisites=["Approval"],
        evidence_references=[],
        requires_human_approval=True,
    )
    assert rec.summary == "Restart the pods"
    assert rec.risk_level == RiskLevel.MEDIUM
    assert rec.model_dump_json()


def test_diagnosis_result_valid():
    rec = RemediationRecommendation(
        summary="Fix",
        steps=["Step 1"],
        risk_level=RiskLevel.LOW,
        prerequisites=[],
        evidence_references=[],
        requires_human_approval=False,
    )
    ref = EvidenceReference(
        incident_id="INC-123",
        relevance_explanation="Related to OOM",
        similarity_scores={"semantic": 0.9},
    )
    res = DiagnosisResult(
        root_cause_explanation="Memory leak in worker",
        confidence=0.9,
        remediation=rec,
        evidence_references=[ref],
        alternative_hypotheses=[],
        missing_evidence=[],
        unsupported_claims=["We need more data on DB load"],
    )
    assert res.confidence == 0.9
    assert res.remediation.summary == "Fix"
    assert res.model_dump_json()


def test_diagnosis_result_invalid_confidence():
    rec = RemediationRecommendation(
        summary="Fix",
        steps=["Step 1"],
        risk_level=RiskLevel.LOW,
        prerequisites=[],
        evidence_references=[],
        requires_human_approval=False,
    )
    with pytest.raises(ValidationError):
        DiagnosisResult(
            root_cause_explanation="Memory leak",
            confidence=-0.1,
            remediation=rec,
            evidence_references=[],
            alternative_hypotheses=[],
            missing_evidence=[],
            unsupported_claims=[],
        )


def test_diagnosis_request_valid():
    summary = IncidentSummaryDTO(
        investigation_id="INV-001",
        narrative="Services down",
        affected_components=["auth"],
        trigger_type=TriggerType.AUTO_DETECTED,
    )
    req = DiagnosisRequest(
        investigation_id="INV-001",
        incident_summary=summary,
        retrieved_evidence=[],
        available_evidence=[],
        trigger_type=TriggerType.AUTO_DETECTED,
        human_description=None,
    )
    assert req.investigation_id == "INV-001"
    assert req.trigger_type == TriggerType.AUTO_DETECTED
    assert req.model_dump_json()


def test_diagnosis_request_missing_required():
    with pytest.raises(ValidationError):
        DiagnosisRequest(
            investigation_id="INV-001"
            # Missing other required fields
        )

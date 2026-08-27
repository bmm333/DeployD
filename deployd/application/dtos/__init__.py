"""
DD-1  deployd.application.dtos
==================================
Public surface of the DTO package.

Consumers (application use-cases, adapters, AI agents) should import exclusively
from this package — never from the individual sub-modules — so that internal
reorganisation never breaks calling code.

    from deployd.application.dtos import DiagnosisRequest, DiagnosisResult, ...
"""

from deployd.application.dtos.diagnosis import (
    AlternativeHypothesis,
    DiagnosisRequest,
    DiagnosisResult,
    RemediationRecommendation,
)
from deployd.application.dtos.enums import EvidenceSource, RiskLevel, TriggerType
from deployd.application.dtos.evidence import EvidenceDTO, MissingEvidence
from deployd.application.dtos.incident_summary import IncidentSummaryDTO
from deployd.application.dtos.investigation_request import (
    ComponentDependencyDTO,
    EventDTO,
    InvestigationRequest,
)
from deployd.application.dtos.retrieval import EvidenceReference, RetrievedEvidence

__all__ = [
    # Enums
    "TriggerType",
    "EvidenceSource",
    "RiskLevel",
    # Investigation
    "EventDTO",
    "ComponentDependencyDTO",
    "InvestigationRequest",
    "IncidentSummaryDTO",
    # Evidence
    "EvidenceDTO",
    "MissingEvidence",
    # Retrieval
    "RetrievedEvidence",
    "EvidenceReference",
    # Diagnosis
    "DiagnosisRequest",
    "DiagnosisResult",
    "RemediationRecommendation",
    "AlternativeHypothesis",
]

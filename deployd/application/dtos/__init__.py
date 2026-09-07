"""
Application-layer DTOs.

These are the typed data contracts that cross the boundary between
the adapters/use-cases and the application layer.  Nothing outside
this package should import from the individual dto modules directly —
import from here instead.

Note: ``InvestigationRequest`` exported here is the ADR-008 Pydantic model
(``AgentInvestigationRequest``).  The legacy three-tier dataclass of the same
name lives in ``investigation_request.py`` and is imported directly by the
three-tier orchestrator tests.
"""

from deployd.application.dtos.diagnosis import (
    AlternativeHypothesis,
    DiagnosisRequest,
    DiagnosisResult,
    DiagnosisTier,
    RemediationRecommendation,
    TierDiagnosisResult,
    TierRemediation,
)
from deployd.application.dtos.enums import EvidenceSource, RiskLevel, TriggerType
from deployd.application.dtos.evidence import EvidenceDTO, MissingEvidence
from deployd.application.dtos.incident_summary import IncidentSummaryDTO
from deployd.application.dtos.investigation_request import (
    AgentInvestigationRequest as InvestigationRequest,
    ComponentDependencyDTO,
    EventDTO,
)
from deployd.application.dtos.retrieval import (
    EvidenceReference,
    RetrievalCandidate,
    RetrievalResult,
    RetrievedEvidence,
)

__all__ = [
    # diagnosis — legacy tier DTOs
    "DiagnosisTier",
    "TierRemediation",
    "TierDiagnosisResult",
    # diagnosis — ADR-008 pipeline DTOs
    "AlternativeHypothesis",
    "DiagnosisRequest",
    "DiagnosisResult",
    "RemediationRecommendation",
    # enums
    "EvidenceSource",
    "RiskLevel",
    "TriggerType",
    # evidence
    "EvidenceDTO",
    "MissingEvidence",
    # incident summary
    "IncidentSummaryDTO",
    # investigation request (ADR-008 pipeline, exported as InvestigationRequest)
    "InvestigationRequest",
    "ComponentDependencyDTO",
    "EventDTO",
    # retrieval — legacy tier DTOs
    "RetrievalCandidate",
    "RetrievalResult",
    # retrieval — ADR-008 pipeline DTOs
    "EvidenceReference",
    "RetrievedEvidence",
]

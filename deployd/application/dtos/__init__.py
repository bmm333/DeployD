"""
Application-layer DTOs.

These are the typed data contracts that cross the boundary between
the adapters/use-cases and the application layer.  Nothing outside
this package should import from the individual dto modules directly —
import from here instead.
"""

from deployd.application.dtos.diagnosis import (
    DiagnosisResult,
    DiagnosisTier,
    RemediationRecommendation,
)
from deployd.application.dtos.investigation_request import InvestigationRequest
from deployd.application.dtos.retrieval import RetrievalCandidate, RetrievalResult

__all__ = [
    "DiagnosisTier",
    "DiagnosisResult",
    "RemediationRecommendation",
    "InvestigationRequest",
    "RetrievalCandidate",
    "RetrievalResult",
]

"""
Detection layer (DID-9).

Deterministic rules that infer new relationships/anomalies (as CoreEvents) from
already-observed evidence combined with already-supplied reference data. Distinct
from Traversal (DID-8): Traversal explores relationships that already exist in the
IncidentGraph; Detection is what produces them. See ADR-004.
"""

from deployd.domain.detection.config_drift_rule import (
    ACTUAL_DEPENDENCY_METADATA_KEY,
    ConfigDriftRule,
)
from deployd.domain.detection.detection_rule import DetectionRule

__all__ = [
    "ACTUAL_DEPENDENCY_METADATA_KEY",
    "ConfigDriftRule",
    "DetectionRule",
]

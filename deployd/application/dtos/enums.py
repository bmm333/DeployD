"""
DD-1
Shared enumerations used across all DTOs in the application layer.
These enums form part of the strict boundary between domain/infra and AI agents.
They must never import from the domain layer — they are DTO-level concepts.
"""

from enum import Enum


class TriggerType(str, Enum):
    """How an investigation was initiated."""

    AUTO_DETECTED = "AUTO_DETECTED"
    ENGINEER_TRIGGERED = "ENGINEER_TRIGGERED"


class EvidenceSource(str, Enum):
    """Origin of a piece of evidence collected during an investigation."""

    SYSTEM_EVENT = "SYSTEM_EVENT"
    HUMAN_OBSERVATION = "HUMAN_OBSERVATION"
    FSM_TRANSITION = "FSM_TRANSITION"


class RiskLevel(str, Enum):
    """Operational risk level associated with a remediation recommendation."""

    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"

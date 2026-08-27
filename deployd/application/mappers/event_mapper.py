"""
ADR-008
EventMapper — converts domain objects into DTO objects.

This is the boundary gate: it is the ONLY place in the application layer
that is allowed to touch ``CoreEvent``, ``CoreEventType``, ``Severity``, or
``IncidentGraph`` domain objects. Everything it produces crosses into DTO
territory and is safe to hand to an AI agent.

Rules (ADR-008):
- Output types must be DTOs only — never domain or ORM objects.
- Field mapping must be explicit; no ``**vars(event)`` shortcuts.
- ``CoreEventType`` and ``Severity`` are flattened to plain strings so that
  the DTO layer stays decoupled from domain enumerations.
"""

from __future__ import annotations

from datetime import datetime, timezone

from deployd.application.dtos.enums import EvidenceSource
from deployd.application.dtos.evidence import EvidenceDTO
from deployd.application.dtos.investigation_request import EventDTO
from deployd.domain.entities.core_event import CoreEvent


class EventMapper:
    """
    Converts ``CoreEvent`` domain objects into application-layer DTOs.

    Instantiate once and reuse — the mapper is stateless.
    """

    # --------------------------------------------------------------------------
    # Public API
    # --------------------------------------------------------------------------

    @staticmethod
    def core_event_to_dto(event: CoreEvent) -> EventDTO:
        """
        Map a single ``CoreEvent`` → ``EventDTO``.

        ``CoreEventType`` and ``Severity`` are serialised as their ``.value``
        strings so that no domain enum escapes the mapper.
        """
        return EventDTO(
            event_id=str(event.event_id),
            timestamp=event.timestamp.astimezone(timezone.utc),
            event_type=event.event_type.value,  # CoreEventType → plain str
            source=event.related_component or "unknown",
            payload={
                "severity": event.severity.value,  # Severity → plain str
                "description": event.description,
                **{k: str(v) for k, v in event.metadata.items()},
            },
        )

    @staticmethod
    def core_event_to_evidence(
        event: CoreEvent,
        *,
        confidence: float = 1.0,
    ) -> EvidenceDTO:
        """
        Map a ``CoreEvent`` → ``EvidenceDTO`` for use in a ``DiagnosisRequest``.

        FSM-generated events are marked as ``FSM_TRANSITION``; human observations
        as ``HUMAN_OBSERVATION``; everything else as ``SYSTEM_EVENT``.

        ``confidence`` defaults to 1.0 for machine-generated events (facts).
        Pass a lower value for events whose accuracy is uncertain.
        """
        from deployd.domain.entities.core_event import CoreEventType  # local import to keep module thin

        source_map = {
            CoreEventType.HUMAN_OBSERVATION: EvidenceSource.HUMAN_OBSERVATION,
            CoreEventType.STATE_CHANGE: EvidenceSource.FSM_TRANSITION,
        }
        evidence_source = source_map.get(event.event_type, EvidenceSource.SYSTEM_EVENT)

        return EvidenceDTO(
            evidence_id=str(event.event_id),
            source=evidence_source,
            description=event.description,
            component=event.related_component or "unknown",
            timestamp=event.timestamp.astimezone(timezone.utc),
            confidence=max(0.0, min(1.0, confidence)),
            raw_data={
                "event_type": event.event_type.value,
                "severity": event.severity.value,
                **{k: str(v) for k, v in event.metadata.items()},
            },
        )

    @classmethod
    def core_events_to_event_dtos(cls, events: list[CoreEvent]) -> list[EventDTO]:
        """Bulk convert a list of ``CoreEvent`` → ``list[EventDTO]``."""
        return [cls.core_event_to_dto(e) for e in events]

    @classmethod
    def core_events_to_evidence(
        cls,
        events: list[CoreEvent],
        *,
        default_confidence: float = 1.0,
    ) -> list[EvidenceDTO]:
        """Bulk convert a list of ``CoreEvent`` → ``list[EvidenceDTO]``."""
        return [cls.core_event_to_evidence(e, confidence=default_confidence) for e in events]

    # --------------------------------------------------------------------------
    # Private helpers
    # --------------------------------------------------------------------------

    @staticmethod
    def _utc_now() -> datetime:
        return datetime.now(timezone.utc)

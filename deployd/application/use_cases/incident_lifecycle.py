"""Incident lifecycle use cases."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from deployd.domain.entities.core_event import CoreEvent, Severity
from deployd.domain.incident.incident import Incident
from deployd.domain.incident.incident_repository import IncidentRepository


class IncidentLifecycleUseCase:
    """Open, update, and close incidents."""

    def __init__(self, repository: IncidentRepository) -> None:
        self._repo = repository

    def ensure_open(self, first_event: CoreEvent) -> Incident:
        """Return the current open incident, opening a new one if needed."""
        current = self._repo.get_current()
        if current is not None:
            return current
        incident = Incident(
            opened_at=first_event.timestamp.replace(tzinfo=timezone.utc)
            if first_event.timestamp.tzinfo is None
            else first_event.timestamp,
        )
        self._repo.save(incident)
        return incident

    def update_severity(self, severity: Severity) -> None:
        current = self._repo.get_current()
        if current is None:
            return
        current.update_severity(severity)
        self._repo.save(current)

    def close_current(
        self,
        graph_snapshot: dict[str, object],
        chat_history: list[dict[str, str]],
        root_cause_summary: str | None = None,
    ) -> Incident | None:
        current = self._repo.get_current()
        if current is None:
            return None
        current.resolve(
            resolved_at=datetime.now(tz=timezone.utc),
            graph_snapshot=graph_snapshot,
            chat_history=chat_history,
            root_cause_summary=root_cause_summary,
        )
        self._repo.save(current)
        return current


class ListIncidentsUseCase:
    """Return all incidents ordered by opened_at desc."""

    def __init__(self, repository: IncidentRepository) -> None:
        self._repo = repository

    def execute(self) -> list[Incident]:
        return self._repo.list_all()


class GetIncidentUseCase:
    """Return a single incident by id."""

    def __init__(self, repository: IncidentRepository) -> None:
        self._repo = repository

    def execute(self, incident_id: uuid.UUID) -> Incident | None:
        return self._repo.get(incident_id)

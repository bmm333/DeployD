"""IncidentRepository port."""

from __future__ import annotations

import uuid
from typing import Protocol, runtime_checkable

from deployd.domain.incident.incident import Incident


@runtime_checkable
class IncidentRepository(Protocol):
    """Persistence port for Incident entities."""

    def save(self, incident: Incident) -> None: ...
    def get(self, incident_id: uuid.UUID) -> Incident | None: ...
    def list_all(self) -> list[Incident]: ...
    def get_current(self) -> Incident | None: ...

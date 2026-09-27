"""Port for the event observation window."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from deployd.domain.entities.core_event import CoreEvent


@runtime_checkable
class EventWindowPort(Protocol):
    """Read/write interface for a time-bounded event observation window."""

    def append(self, event: CoreEvent) -> None: ...

    def snapshot(self) -> list[CoreEvent]: ...

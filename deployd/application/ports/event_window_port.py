"""Port for the event observation window."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from deployd.domain.entities.core_event import CoreEvent


@runtime_checkable
class EventWindowPort(Protocol):
    """
    Read/write interface for a time-bounded event observation window.

    Methods
    -------
    append(event)
        Add ``event`` to the window.  Implementations are responsible for
        pruning events that fall outside the configured time boundary.

    snapshot() -> list[CoreEvent]
        Return an immutable snapshot of all events currently in the window,
        ordered by insertion (oldest first).  The list is a copy — callers may
        safely iterate or filter it without affecting the window state.
    """

    def append(self, event: CoreEvent) -> None: ...

    def snapshot(self) -> list[CoreEvent]: ...

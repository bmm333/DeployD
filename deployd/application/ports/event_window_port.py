"""
deployd/application/ports/event_window_port.py

Port (outgoing secondary port) for the event observation window.

The ``EventWindowPort`` Protocol decouples the ``CorrelateEventsUseCase`` from
any concrete buffer implementation.  Concretions live in the infrastructure
layer (``deployd/infrastructure/streaming/``).

Production swap path
--------------------
Today:   SlidingWindow (in-memory deque, no external dependencies)
Future:  RedisStreamsWindow (XRANGE query over Redis Streams time-series)

The use case imports ``EventWindowPort`` only.  Swapping the implementation
requires no changes to any application or domain file.
"""

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

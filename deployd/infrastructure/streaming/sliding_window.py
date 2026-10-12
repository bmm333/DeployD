"""
Concrete implementation of ``EventWindowPort``: an in-memory, event-time window.
"""

from __future__ import annotations

import bisect
from datetime import timedelta

from deployd.domain.entities.core_event import CHANGE_EVENT_TYPES, CoreEvent

DEFAULT_WINDOW_SECONDS: float = 5 * 60  # 5 minutes


class SlidingWindow:
    """Events within ``window_seconds`` of the newest event seen, ordered by timestamp.

    The window follows event time, not the wall clock, so replayed or late-shipped
    events correlate the same way as live ones. Events older than the window are dropped,
    except change events (deploys, config and host changes), which are kept for
    ``change_retention_seconds`` because a change can precede its effect by hours.
    """

    def __init__(
        self,
        window_seconds: float = DEFAULT_WINDOW_SECONDS,
        change_retention_seconds: float | None = None,
    ) -> None:
        self._window = timedelta(seconds=window_seconds)
        self._change_retention = timedelta(
            seconds=max(window_seconds, change_retention_seconds or 0.0)
        )
        self._events: list[CoreEvent] = []

    def append(self, event: CoreEvent) -> None:
        """Insert *event* in time order and prune events outside the window."""
        bisect.insort(self._events, event, key=lambda e: e.timestamp)
        self._prune()

    def snapshot(self) -> list[CoreEvent]:
        """Return a copy of all events currently in the window (oldest first)."""
        return list(self._events)

    def _prune(self) -> None:
        newest = self._events[-1].timestamp
        cutoff = newest - self._window
        change_cutoff = newest - self._change_retention
        self._events = [
            e
            for e in self._events
            if e.timestamp >= (change_cutoff if e.event_type in CHANGE_EVENT_TYPES else cutoff)
        ]

    def __len__(self) -> int:
        return len(self._events)

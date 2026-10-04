"""
Concrete implementation of ``EventWindowPort``: an in-memory, event-time window.
"""

from __future__ import annotations

import bisect
from datetime import timedelta

from deployd.domain.entities.core_event import CoreEvent

DEFAULT_WINDOW_SECONDS: float = 5 * 60  # 5 minutes


class SlidingWindow:
    """Events within ``window_seconds`` of the newest event seen, ordered by timestamp.

    The window follows event time, not the wall clock, so replayed or late-shipped
    events correlate the same way as live ones. Events older than the window are dropped.
    """

    def __init__(self, window_seconds: float = DEFAULT_WINDOW_SECONDS) -> None:
        self._window = timedelta(seconds=window_seconds)
        self._events: list[CoreEvent] = []

    def append(self, event: CoreEvent) -> None:
        """Insert *event* in time order and prune events outside the window."""
        bisect.insort(self._events, event, key=lambda e: e.timestamp)
        self._prune()

    def snapshot(self) -> list[CoreEvent]:
        """Return a copy of all events currently in the window (oldest first)."""
        return list(self._events)

    def _prune(self) -> None:
        cutoff = self._events[-1].timestamp - self._window
        first_kept = bisect.bisect_left(self._events, cutoff, key=lambda e: e.timestamp)
        del self._events[:first_kept]

    def __len__(self) -> int:
        return len(self._events)

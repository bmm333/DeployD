"""
Concrete implementation of ``EventWindowPort`` backed by an in-memory deque.
"""

from __future__ import annotations

from collections import deque
from datetime import datetime, timedelta, timezone

from deployd.domain.entities.core_event import CoreEvent

DEFAULT_WINDOW_SECONDS: float = 5 * 60  # 5 minutes


class SlidingWindow:
    """
    An in-memory, time-bounded deque of ``CoreEvent`` objects.

    Events older than ``window_seconds`` from *now* are pruned on every
    ``append`` call.  Memory usage is O(events within the window).

    Parameters
    ----------
    window_seconds
        Width of the observation window in seconds.  Defaults to 300 (5 min).
        cascading failures propagate slowly through dependency chains.
    """

    def __init__(self, window_seconds: float = DEFAULT_WINDOW_SECONDS) -> None:
        self._window = timedelta(seconds=window_seconds)
        self._deque: deque[CoreEvent] = deque()

    def append(self, event: CoreEvent) -> None:
        """Add *event* to the window and prune expired events."""
        self._deque.append(event)
        self._prune()

    def snapshot(self) -> list[CoreEvent]:
        """Return a copy of all events currently in the window (oldest first)."""
        return list(self._deque)

    def _prune(self) -> None:
        cutoff = datetime.now(timezone.utc) - self._window
        while self._deque and self._deque[0].timestamp < cutoff:
            self._deque.popleft()

    def __len__(self) -> int:
        return len(self._deque)

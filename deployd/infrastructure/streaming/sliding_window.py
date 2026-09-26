"""
deployd/infrastructure/streaming/sliding_window.py

Concrete implementation of ``EventWindowPort`` backed by an in-memory deque.

This is the **only** file in the event correlation stack that is allowed to
call ``datetime.now(timezone.utc)``.  System-clock coupling is an
infrastructure concern — it belongs here, not in the domain or application
layers.

Production swap path
--------------------
Replace the body of ``SlidingWindow`` with a Redis Streams ``XRANGE`` query
(e.g. ``XRANGE events - + COUNT 1000 MINID <cutoff_ms>``).  The
``EventWindowPort`` interface is satisfied identically — no other file changes.

    class RedisStreamsWindow:
        def append(self, event: CoreEvent) -> None:
            self._client.xadd("events", _serialise(event))

        def snapshot(self) -> list[CoreEvent]:
            cutoff = (datetime.now(timezone.utc) - self._window).timestamp()
            raw = self._client.xrange("events", min=f"{int(cutoff*1000)}-0")
            return [_deserialise(r) for _, r in raw]
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
        5 minutes is the standard correlation window for distributed systems
        where cascading failures propagate slowly through dependency chains.
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

    # ------------------------------------------------------------------
    # Private
    # ------------------------------------------------------------------

    def _prune(self) -> None:
        cutoff = datetime.now(timezone.utc) - self._window
        while self._deque and self._deque[0].timestamp < cutoff:
            self._deque.popleft()

    def __len__(self) -> int:
        return len(self._deque)

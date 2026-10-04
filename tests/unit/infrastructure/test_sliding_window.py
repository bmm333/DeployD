"""SlidingWindow keeps events by event time, independent of the wall clock."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from deployd.domain.entities.core_event import CoreEvent, CoreEventType, Severity
from deployd.infrastructure.streaming.sliding_window import SlidingWindow

# Far in the past on purpose: a wall-clock window would drop every one of these.
T0 = datetime(2020, 1, 1, 12, 0, 0, tzinfo=timezone.utc)


def _event(seconds: float, description: str = "") -> CoreEvent:
    return CoreEvent(
        event_type=CoreEventType.STATE_CHANGE,
        severity=Severity.INFO,
        timestamp=T0 + timedelta(seconds=seconds),
        description=description or f"t+{seconds}",
    )


def _descriptions(window: SlidingWindow) -> list[str]:
    return [e.description for e in window.snapshot()]


def test_old_events_are_kept_while_inside_the_window() -> None:
    window = SlidingWindow(window_seconds=300)
    for t in (0, 60, 120):
        window.append(_event(t))

    assert _descriptions(window) == ["t+0", "t+60", "t+120"]


def test_events_older_than_newest_minus_window_are_pruned() -> None:
    window = SlidingWindow(window_seconds=300)
    for t in (0, 100, 350):
        window.append(_event(t))

    assert _descriptions(window) == ["t+100", "t+350"]
    assert len(window) == 2


def test_event_exactly_at_the_window_edge_is_kept() -> None:
    window = SlidingWindow(window_seconds=300)
    window.append(_event(0))
    window.append(_event(300))

    assert _descriptions(window) == ["t+0", "t+300"]


def test_out_of_order_event_is_inserted_in_time_order() -> None:
    window = SlidingWindow(window_seconds=300)
    for t in (0, 200, 100):
        window.append(_event(t))

    assert _descriptions(window) == ["t+0", "t+100", "t+200"]


def test_late_event_older_than_the_window_is_dropped() -> None:
    window = SlidingWindow(window_seconds=300)
    window.append(_event(1000))
    window.append(_event(10))

    assert _descriptions(window) == ["t+1000"]


def test_snapshot_is_a_copy() -> None:
    window = SlidingWindow(window_seconds=300)
    window.append(_event(0))
    window.snapshot().clear()

    assert len(window) == 1

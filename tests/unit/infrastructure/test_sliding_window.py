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


def _change(seconds: float, description: str) -> CoreEvent:
    return CoreEvent(
        event_type=CoreEventType.CONFIG_CHANGE,
        severity=Severity.INFO,
        timestamp=T0 + timedelta(seconds=seconds),
        description=description,
    )


def test_change_events_outlive_the_window_up_to_their_retention() -> None:
    window = SlidingWindow(window_seconds=300, change_retention_seconds=3600)
    window.append(_change(0, "os upgrade"))
    window.append(_event(10, "old symptom"))
    window.append(_event(1800, "recent symptom"))

    assert _descriptions(window) == ["os upgrade", "recent symptom"]


def test_change_events_are_dropped_after_their_retention() -> None:
    window = SlidingWindow(window_seconds=300, change_retention_seconds=3600)
    window.append(_change(0, "os upgrade"))
    window.append(_event(3601, "symptom"))

    assert _descriptions(window) == ["symptom"]


def test_without_a_retention_change_events_follow_the_window() -> None:
    window = SlidingWindow(window_seconds=300)
    window.append(_change(0, "os upgrade"))
    window.append(_event(1800, "symptom"))

    assert _descriptions(window) == ["symptom"]


def test_a_retention_shorter_than_the_window_never_shrinks_it() -> None:
    window = SlidingWindow(window_seconds=300, change_retention_seconds=10)
    window.append(_change(0, "os upgrade"))
    window.append(_event(200, "symptom"))

    assert _descriptions(window) == ["os upgrade", "symptom"]


def test_the_record_of_the_changes_in_force_at_a_failure_is_kept_like_a_change() -> None:
    window = SlidingWindow(window_seconds=300, change_retention_seconds=3600)
    window.append(
        CoreEvent(
            event_type=CoreEventType.SYMPTOM_OBSERVED,
            severity=Severity.INFO,
            timestamp=T0,
            description="failing under os_upgrade",
        )
    )
    window.append(_event(1800, "recent symptom"))

    assert _descriptions(window) == ["failing under os_upgrade", "recent symptom"]

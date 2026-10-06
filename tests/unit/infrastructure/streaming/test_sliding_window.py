import uuid
from datetime import datetime, timedelta, timezone

from deployd.domain.entities.core_event import CoreEvent, CoreEventType, Severity
from deployd.infrastructure.streaming.sliding_window import SlidingWindow

T0 = datetime(2026, 9, 30, 10, 0, tzinfo=timezone.utc)


def _ev(seconds_offset: int) -> CoreEvent:
    return CoreEvent(
        event_id=uuid.uuid4(),
        event_type=CoreEventType.STATE_CHANGE,
        severity=Severity.ERROR,
        timestamp=T0 + timedelta(seconds=seconds_offset),
        related_component="test",
        description="test",
    )


def test_sliding_window_event_time_pruning():
    window = SlidingWindow(window_seconds=60)

    e1 = _ev(0)
    e2 = _ev(30)
    e3 = _ev(65)

    window.append(e1)
    assert len(window) == 1

    window.append(e2)
    assert len(window) == 2

    # Adding e3 should prune e1 because e3 is 65s after e1, and window is 60s
    window.append(e3)
    assert len(window) == 2

    snap = window.snapshot()
    assert snap[0].event_id == e2.event_id
    assert snap[1].event_id == e3.event_id


def test_sliding_window_out_of_order():
    window = SlidingWindow(window_seconds=60)

    e1 = _ev(30)
    e2 = _ev(0)  # Out of order, older
    e3 = _ev(95)  # Will prune e2

    window.append(e1)
    window.append(e2)

    snap = window.snapshot()
    # Should be sorted
    assert snap[0].event_id == e2.event_id
    assert snap[1].event_id == e1.event_id

    window.append(e3)
    # E3 is 95, window is 60, cutoff is 35. So e2 (0) and e1 (30) should be pruned!
    assert len(window) == 1
    assert window.snapshot()[0].event_id == e3.event_id

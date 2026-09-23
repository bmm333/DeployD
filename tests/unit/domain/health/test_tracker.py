from datetime import datetime, timedelta, timezone

from deployd.domain.entities.core_event import CoreEvent, CoreEventType, Severity
from deployd.domain.health.process_state import ProcessHealthStatus
from deployd.domain.health.tracker import ComponentHealthTracker


def test_tracker_returns_healthy_for_normal_event() -> None:
    tracker = ComponentHealthTracker()
    event = CoreEvent(
        event_type=CoreEventType.HEALTH_CHECK_PASS,
        timestamp=datetime.now(timezone.utc),
        severity=Severity.INFO,
        description="OK",
        related_component="test-comp",
    )

    trigger, state, events = tracker.process_event(event)

    assert trigger is False
    assert state == ProcessHealthStatus.HEALTHY
    assert len(events) == 1


def test_tracker_triggers_on_crashing() -> None:
    tracker = ComponentHealthTracker(cooldown_window_s=300)

    dt = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    event1 = CoreEvent(
        event_type=CoreEventType.PROCESS_CRASH,
        timestamp=dt,
        severity=Severity.CRITICAL,
        description="Crash",
        related_component="test-comp",
    )

    trigger, state, events = tracker.process_event(event1)

    assert trigger is True
    assert state == ProcessHealthStatus.CRASHING


def test_tracker_respects_cooldown() -> None:
    tracker = ComponentHealthTracker(cooldown_window_s=300)

    dt = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    # First crash triggers
    event1 = CoreEvent(
        event_type=CoreEventType.PROCESS_CRASH,
        timestamp=dt,
        severity=Severity.CRITICAL,
        description="Crash 1",
        related_component="test-comp",
    )
    trigger1, _, _ = tracker.process_event(event1)
    assert trigger1 is True

    # Second crash within cooldown does not trigger
    event2 = CoreEvent(
        event_type=CoreEventType.PROCESS_CRASH,
        timestamp=dt + timedelta(seconds=100),
        severity=Severity.CRITICAL,
        description="Crash 2",
        related_component="test-comp",
    )
    trigger2, _, _ = tracker.process_event(event2)
    assert trigger2 is False

    # Third crash after cooldown triggers again
    event3 = CoreEvent(
        event_type=CoreEventType.PROCESS_CRASH,
        timestamp=dt + timedelta(seconds=301),
        severity=Severity.CRITICAL,
        description="Crash 3",
        related_component="test-comp",
    )
    trigger3, state3, _ = tracker.process_event(event3)
    assert trigger3 is True
    # In a real FSM, this might be CRASH_LOOP by now depending on restarts, but definitely CRASHING/CRASH_LOOP
    assert state3 in (ProcessHealthStatus.CRASHING, ProcessHealthStatus.CRASH_LOOP)


def test_tracker_evicts_old_events() -> None:
    tracker = ComponentHealthTracker(max_events_per_component=2)

    dt = datetime.now(timezone.utc)
    for i in range(3):
        tracker.process_event(
            CoreEvent(
                event_type=CoreEventType.HEALTH_CHECK_PASS,
                timestamp=dt,
                severity=Severity.INFO,
                description=f"Msg {i}",
                related_component="test-comp",
            )
        )

    # Should only keep the last 2
    _, _, events = tracker.process_event(
        CoreEvent(
            event_type=CoreEventType.HEALTH_CHECK_PASS,
            timestamp=dt,
            severity=Severity.INFO,
            description="Msg 3",
            related_component="test-comp",
        )
    )

    assert len(events) == 2
    assert events[-1].description == "Msg 3"
    assert events[0].description == "Msg 2"

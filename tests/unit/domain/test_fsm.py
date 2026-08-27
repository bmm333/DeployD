"""
Test suite for ProcessHealthFSM.
Coverage target: every cell in the ADR-006 transition table = one test class.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from deployd.domain.entities.core_event import CoreEvent, CoreEventType, Severity
from deployd.domain.health.process_health import (
    OutOfOrderEventError,
    ProcessHealthFSM,
)
from deployd.domain.health.process_state import ProcessHealthStatus

# helpers & consts
T0 = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
RECOVERY = timedelta(minutes=5)
MAX_RESTARTS = 3
WINDOW = timedelta(minutes=10)


def fsm() -> ProcessHealthFSM:
    return ProcessHealthFSM(
        recovery_window=RECOVERY,
        max_restart_count=MAX_RESTARTS,
        restart_time_window=WINDOW,
    )


def event(
    event_type: CoreEventType,
    severity: Severity,
    offset_seconds: int = 0,
    metadata: dict | None = None,
) -> CoreEvent:
    return CoreEvent(
        event_id=uuid.uuid4(),
        event_type=event_type,
        severity=severity,
        timestamp=T0 + timedelta(seconds=offset_seconds),
        description="test",
        metadata=metadata or {},
    )


# ADR-006 alphabet
def warn_resource(offset: int = 0) -> CoreEvent:
    return event(CoreEventType.RESOURCE_EXHAUSTION, Severity.WARNING, offset)


def warn_health(offset: int = 0) -> CoreEvent:
    return event(CoreEventType.HEALTH_CHECK_FAIL, Severity.WARNING, offset)


def crit_crash(offset: int = 0) -> CoreEvent:
    return event(CoreEventType.PROCESS_CRASH, Severity.CRITICAL, offset)


def crit_dep_fail(offset: int = 0) -> CoreEvent:
    return event(CoreEventType.DEPENDENCY_FAILURE, Severity.CRITICAL, offset)


def restart_deploy(offset: int = 0) -> CoreEvent:
    return event(CoreEventType.DEPLOY_STARTED, Severity.INFO, offset)


def restart_state_change(offset: int = 0) -> CoreEvent:
    return event(CoreEventType.STATE_CHANGE, Severity.INFO, offset, metadata={"is_restart": True})


def state_change_no_restart(offset: int = 0) -> CoreEvent:
    return event(CoreEventType.STATE_CHANGE, Severity.INFO, offset, metadata={})


def manual_reset(offset: int = 0) -> CoreEvent:
    return event(
        CoreEventType.HUMAN_OBSERVATION, Severity.INFO, offset, metadata={"action": "MANUAL_RESET"}
    )


def irrelevant(offset: int = 0) -> CoreEvent:
    return event(CoreEventType.CONFIG_CHANGE, Severity.INFO, offset)


# Config
class TestInit:
    def test_initial_state_is_healthy(self) -> None:
        assert fsm().state == ProcessHealthStatus.HEALTHY

    def test_transition_history_empty_on_init(self) -> None:
        assert fsm().transition_history == []

    def test_restart_count_zero_on_init(self) -> None:
        assert fsm().restart_attempt_count == 0

    def test_invalid_recovery_window_raises(self) -> None:
        with pytest.raises(ValueError, match="recovery_window"):
            ProcessHealthFSM(
                recovery_window=timedelta(seconds=0),
                max_restart_count=3,
                restart_time_window=WINDOW,
            )

    def test_invalid_max_restart_count_raises(self) -> None:
        with pytest.raises(ValueError, match="max_restart_count"):
            ProcessHealthFSM(
                recovery_window=RECOVERY,
                max_restart_count=0,
                restart_time_window=WINDOW,
            )

    def test_invalid_restart_time_window_raises(self) -> None:
        with pytest.raises(ValueError, match="restart_time_window"):
            ProcessHealthFSM(
                recovery_window=RECOVERY,
                max_restart_count=3,
                restart_time_window=timedelta(seconds=-1),
            )


# ADR-006 Row-1 (Healthy state)


class TestHealthyState:
    def test_warn_resource_exhaustion_to_degraded(self) -> None:
        m = fsm()
        m.process_event(warn_resource())
        assert m.state == ProcessHealthStatus.DEGRADED

    def test_warn_health_check_fail_to_degraded(self) -> None:
        m = fsm()
        m.process_event(warn_health())
        assert m.state == ProcessHealthStatus.DEGRADED

    def test_crit_crash_to_crashing(self) -> None:
        m = fsm()
        m.process_event(crit_crash())
        assert m.state == ProcessHealthStatus.CRASHING

    def test_crit_dep_fail_to_crashing(self) -> None:
        m = fsm()
        m.process_event(crit_dep_fail())
        assert m.state == ProcessHealthStatus.CRASHING

    def test_restart_is_noop(self) -> None:
        m = fsm()
        m.process_event(restart_deploy())
        assert m.state == ProcessHealthStatus.HEALTHY

    def test_reset_is_noop(self) -> None:
        m = fsm()
        m.process_event(manual_reset())
        assert m.state == ProcessHealthStatus.HEALTHY

    def test_irrelevant_event_is_noop(self) -> None:
        m = fsm()
        m.process_event(irrelevant())
        assert m.state == ProcessHealthStatus.HEALTHY


# ADR-006 Row-2 (Degraded state)
class TestDegradedState:
    def setup_method(self) -> None:
        self.m = fsm()
        self.m.process_event(warn_resource(0))
        assert self.m.state == ProcessHealthStatus.DEGRADED

    def test_crit_crash_to_crashing(self) -> None:
        self.m.process_event(crit_crash(10))
        assert self.m.state == ProcessHealthStatus.CRASHING

    def test_crit_dep_fail_to_crashing(self) -> None:
        self.m.process_event(crit_dep_fail(10))
        assert self.m.state == ProcessHealthStatus.CRASHING

    def test_warn_is_noop(self) -> None:
        self.m.process_event(warn_health(10))
        assert self.m.state == ProcessHealthStatus.DEGRADED

    def test_restart_is_noop(self) -> None:
        self.m.process_event(restart_deploy(10))
        assert self.m.state == ProcessHealthStatus.DEGRADED

    def test_reset_is_noop(self) -> None:
        self.m.process_event(manual_reset(10))
        assert self.m.state == ProcessHealthStatus.DEGRADED

    def test_irrelevant_is_noop(self) -> None:
        self.m.process_event(irrelevant(10))
        assert self.m.state == ProcessHealthStatus.DEGRADED


# ADR-006 Row-3 (Crashing state)


class TestCrashingState:
    def setup_method(self) -> None:
        self.m = fsm()
        self.m.process_event(crit_crash(0))
        assert self.m.state == ProcessHealthStatus.CRASHING

    def test_deploy_started_to_restarting(self) -> None:
        self.m.process_event(restart_deploy(10))
        assert self.m.state == ProcessHealthStatus.RESTARTING

    def test_state_change_with_is_restart_to_restarting(self) -> None:
        self.m.process_event(restart_state_change(10))
        assert self.m.state == ProcessHealthStatus.RESTARTING

    def test_state_change_without_is_restart_is_noop(self) -> None:
        self.m.process_event(state_change_no_restart(10))
        assert self.m.state == ProcessHealthStatus.CRASHING

    def test_warn_is_noop(self) -> None:
        self.m.process_event(warn_resource(10))
        assert self.m.state == ProcessHealthStatus.CRASHING

    def test_crit_crash_is_noop(self) -> None:
        self.m.process_event(crit_crash(10))
        assert self.m.state == ProcessHealthStatus.CRASHING

    def test_reset_is_noop(self) -> None:
        self.m.process_event(manual_reset(10))
        assert self.m.state == ProcessHealthStatus.CRASHING

    def test_irrelevant_is_noop(self) -> None:
        self.m.process_event(irrelevant(10))
        assert self.m.state == ProcessHealthStatus.CRASHING

    def test_restart_records_attempt(self) -> None:
        self.m.process_event(restart_deploy(10))
        assert self.m.restart_attempt_count == 1


# ADR-006 Row-4 (Restarting state)
class TestRestartingState:
    def setup_method(self) -> None:
        self.m = fsm()
        self.m.process_event(crit_crash(0))
        self.m.process_event(restart_deploy(10))
        assert self.m.state == ProcessHealthStatus.RESTARTING

    def test_crit_crash_to_crashing(self) -> None:
        self.m.process_event(crit_crash(20))
        assert self.m.state == ProcessHealthStatus.CRASHING

    def test_dep_failure_is_noop(self) -> None:
        self.m.process_event(crit_dep_fail(20))
        assert self.m.state == ProcessHealthStatus.RESTARTING

    def test_recovery_window_elapsed_to_healthy(self) -> None:
        self.m.process_event(irrelevant(offset=310 + 10))
        assert self.m.state == ProcessHealthStatus.HEALTHY

    def test_recovery_window_not_elapsed_stays_restarting(self) -> None:
        self.m.process_event(irrelevant(offset=20))
        assert self.m.state == ProcessHealthStatus.RESTARTING

    def test_restart_within_window_stays_restarting(self) -> None:
        self.m.process_event(restart_deploy(20))
        assert self.m.state == ProcessHealthStatus.RESTARTING

    def test_warn_is_noop(self) -> None:
        self.m.process_event(warn_resource(20))
        assert self.m.state == ProcessHealthStatus.RESTARTING

    def test_reset_is_noop(self) -> None:
        self.m.process_event(manual_reset(20))
        assert self.m.state == ProcessHealthStatus.RESTARTING

    def test_crit_crash_priority_over_recovery_window(self) -> None:
        # Even if recovery window elapsed, CRIT_CRASH takes priority
        self.m.process_event(crit_crash(offset=310 + 10))
        assert self.m.state == ProcessHealthStatus.CRASHING

    def test_recovery_clears_restart_history(self) -> None:
        self.m.process_event(irrelevant(offset=310 + 10))
        assert self.m.state == ProcessHealthStatus.HEALTHY
        assert self.m.restart_attempt_count == 0


# ADR-006 Row-5 (Crash loop state)
class TestCrashLoopState:
    def _reach_crash_loop(self) -> ProcessHealthFSM:
        m = fsm()
        m.process_event(crit_crash(0))
        m.process_event(restart_deploy(10))
        m.process_event(crit_crash(20))
        m.process_event(restart_deploy(30))
        m.process_event(crit_crash(40))
        m.process_event(restart_deploy(50))
        m.process_event(crit_crash(60))
        m.process_event(restart_deploy(70))
        assert m.state == ProcessHealthStatus.CRASH_LOOP
        return m

    def test_manual_reset_to_healthy(self) -> None:
        m = self._reach_crash_loop()
        m.process_event(manual_reset(80))
        assert m.state == ProcessHealthStatus.HEALTHY

    def test_manual_reset_clears_restart_history(self) -> None:
        m = self._reach_crash_loop()
        m.process_event(manual_reset(80))
        assert m.restart_attempt_count == 0

    def test_crit_crash_is_noop(self) -> None:
        m = self._reach_crash_loop()
        m.process_event(crit_crash(80))
        assert m.state == ProcessHealthStatus.CRASH_LOOP

    def test_restart_is_noop(self) -> None:
        m = self._reach_crash_loop()
        m.process_event(restart_deploy(80))
        assert m.state == ProcessHealthStatus.CRASH_LOOP

    def test_warn_is_noop(self) -> None:
        m = self._reach_crash_loop()
        m.process_event(warn_resource(80))
        assert m.state == ProcessHealthStatus.CRASH_LOOP

    def test_irrelevant_is_noop(self) -> None:
        m = self._reach_crash_loop()
        m.process_event(irrelevant(80))
        assert m.state == ProcessHealthStatus.CRASH_LOOP


# Transition history and metadata
class TestTransitionHistory:
    def test_transition_recorded(self) -> None:
        m = fsm()
        m.process_event(warn_resource(0))
        history = m.transition_history
        assert len(history) == 1
        assert history[0].from_state == ProcessHealthStatus.HEALTHY
        assert history[0].to_state == ProcessHealthStatus.DEGRADED

    def test_transition_history_is_copy(self) -> None:
        m = fsm()
        m.process_event(warn_resource(0))
        h1 = m.transition_history
        h1.clear()
        assert len(m.transition_history) == 1

    def test_noop_event_does_not_record_transition(self) -> None:
        m = fsm()
        m.process_event(irrelevant(0))
        assert m.transition_history == []

    def test_transition_event_id_matches(self) -> None:
        m = fsm()
        e = warn_resource(0)
        m.process_event(e)
        assert m.transition_history[0].event_id == e.event_id


# Chronological order
class TestChronologicalOrder:
    def test_out_of_order_raises(self) -> None:
        m = fsm()
        m.process_event(warn_resource(100))
        with pytest.raises(OutOfOrderEventError):
            m.process_event(warn_resource(50))

    def test_same_timestamp_is_allowed(self) -> None:
        m = fsm()
        m.process_event(warn_resource(100))
        # Should not raise
        m.process_event(irrelevant(100))

    def test_first_event_any_timestamp_allowed(self) -> None:
        m = fsm()
        m.process_event(warn_resource(999))
        assert m.state == ProcessHealthStatus.DEGRADED


# Restart window
class TestRestartWindowPruning:
    def test_old_restarts_pruned_outside_window(self) -> None:
        """Restarts older than restart_time_window (10 min) should not count."""
        m = fsm()

        m.process_event(crit_crash(0))
        m.process_event(restart_deploy(10))
        m.process_event(crit_crash(20))
        m.process_event(restart_deploy(30))
        m.process_event(crit_crash(40))
        m.process_event(restart_deploy(50))
        m.process_event(crit_crash(60))
        m.process_event(restart_deploy(620))
        assert m.state == ProcessHealthStatus.RESTARTING


class TestDeterminism:
    def test_same_sequence_same_result(self) -> None:
        events = [
            warn_resource(0),
            crit_crash(10),
            restart_deploy(20),
            crit_crash(30),
            restart_deploy(40),
        ]

        m1 = fsm()
        m2 = fsm()

        for e in events:
            m1.process_event(e)
            m2.process_event(e)

        assert m1.state == m2.state
        assert m1.restart_attempt_count == m2.restart_attempt_count

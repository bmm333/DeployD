from __future__ import annotations

import uuid  # noqa: TCH003
from dataclasses import dataclass
from datetime import datetime, timedelta  # noqa: TCH003

from deployd.domain.entities.core_event import CoreEvent, CoreEventType, Severity
from deployd.domain.health.process_state import ProcessHealthStatus


class ProcessHealthFSMError(Exception):
    """BBase class for PHF domain errors"""


class OutOfOrderEventError(ProcessHealthFSMError):
    """Events must be processed chronologically"""


@dataclass(frozen=True)
class Transition:
    event_id: uuid.UUID
    timestamp: datetime
    from_state: ProcessHealthStatus
    to_state: ProcessHealthStatus


class ProcessHealthFSM:
    """Deterministic state machine for process Health
    next state is a function of current state event restart history and config threshol.
    this is intentional see ADR-005"""

    def __init__(
        self,
        *,
        recovery_window: timedelta,
        max_restart_count: int,
        restart_time_window: timedelta,
    ) -> None:
        if recovery_window.total_seconds() <= 0:
            raise ValueError("recovery_window must be > 0")
        if max_restart_count <= 0:
            raise ValueError("max_restart_count must be > 0")
        if restart_time_window.total_seconds() <= 0:
            raise ValueError("restart_time_window must be > 0")

        self._recovery_window = recovery_window
        self._max_restart_count = max_restart_count
        self._restart_time_window = restart_time_window
        self._current_state = ProcessHealthStatus.HEALTHY
        self._last_event_time: datetime | None = None
        self._restarting_since: datetime | None = None
        self._restart_timestamps: list[datetime] = []
        self._transition_history: list[Transition] = []

    @property
    def state(self) -> ProcessHealthStatus:
        return self._current_state

    @property
    def transition_history(self) -> list[Transition]:
        return list(self._transition_history)

    @property
    def restart_attempt_count(self) -> int:
        return len(self._restart_timestamps)

    @staticmethod
    def _is_warn(event: CoreEvent) -> bool:
        return event.severity == Severity.WARNING and event.event_type in (
            CoreEventType.RESOURCE_EXHAUSTION,
            CoreEventType.HEALTH_CHECK_FAIL,
        )

    @staticmethod
    def _is_crit_fail(event: CoreEvent) -> bool:
        return event.severity == Severity.CRITICAL and event.event_type in (
            CoreEventType.PROCESS_CRASH,
            CoreEventType.DEPENDENCY_FAILURE,
        )

    @staticmethod
    def _is_crit_crash(event: CoreEvent) -> bool:
        return (
            event.severity == Severity.CRITICAL and event.event_type == CoreEventType.PROCESS_CRASH
        )

    @staticmethod
    def _is_restart(event: CoreEvent) -> bool:
        if event.event_type == CoreEventType.DEPLOY_STARTED:
            return True
        if event.event_type == CoreEventType.STATE_CHANGE:
            return bool(event.metadata.get("is_restart"))
        return False

    @staticmethod
    def _is_reset(event: CoreEvent) -> bool:
        return (
            event.event_type == CoreEventType.HUMAN_OBSERVATION
            and event.metadata.get("action") == "MANUAL_RESET"
        )

    def process_event(self, event: CoreEvent) -> ProcessHealthStatus:
        self._check_chronological_order(event)

        if self._current_state == ProcessHealthStatus.RESTARTING:
            self._handle_restarting(event)
        elif self._current_state == ProcessHealthStatus.CRASH_LOOP:
            self._handle_crash_loop(event)
        elif self._current_state == ProcessHealthStatus.HEALTHY:
            self._handle_healthy(event)
        elif self._current_state == ProcessHealthStatus.DEGRADED:
            self._handle_degraded(event)
        elif self._current_state == ProcessHealthStatus.CRASHING:
            self._handle_crashing(event)

        return self._current_state

    def _handle_healthy(self, event: CoreEvent) -> None:
        if self._is_crit_fail(event):
            self._transition_to(ProcessHealthStatus.CRASHING, event)
        elif self._is_warn(event):
            self._transition_to(ProcessHealthStatus.DEGRADED, event)

    def _handle_degraded(self, event: CoreEvent) -> None:
        if self._is_crit_fail(event):
            self._transition_to(ProcessHealthStatus.CRASHING, event)

    def _handle_crashing(self, event: CoreEvent) -> None:
        if self._is_restart(event):
            self._handle_restart_attempt(event)

    def _handle_restarting(self, event: CoreEvent) -> None:
        if self._is_crit_crash(event):
            self._transition_to(ProcessHealthStatus.CRASHING, event)
            return
        if self._recovery_window_elapsed(event.timestamp):
            self._transition_to(ProcessHealthStatus.HEALTHY, event)
            return
        if self._is_restart(event):
            self._handle_restart_attempt(event)

    def _handle_crash_loop(self, event: CoreEvent) -> None:
        if self._is_reset(event):
            self._transition_to(ProcessHealthStatus.HEALTHY, event)

    def _handle_restart_attempt(self, event: CoreEvent) -> None:
        cutoff = event.timestamp - self._restart_time_window
        self._restart_timestamps = [t for t in self._restart_timestamps if t >= cutoff]
        self._restart_timestamps.append(event.timestamp)
        if len(self._restart_timestamps) > self._max_restart_count:
            self._transition_to(ProcessHealthStatus.CRASH_LOOP, event)
        else:
            self._transition_to(ProcessHealthStatus.RESTARTING, event)

    def _transition_to(self, new_state: ProcessHealthStatus, event: CoreEvent) -> None:
        old_state = self._current_state
        self._current_state = new_state
        self._transition_history.append(
            Transition(
                event_id=event.event_id,
                timestamp=event.timestamp,
                from_state=old_state,
                to_state=new_state,
            )
        )

        if new_state == ProcessHealthStatus.RESTARTING:
            self._restarting_since = event.timestamp
        else:
            self._restarting_since = None

        if new_state == ProcessHealthStatus.HEALTHY:
            self._restart_timestamps = []

    def _recovery_window_elapsed(self, timestamp: datetime) -> bool:
        if self._restarting_since is None:
            return False
        return (timestamp - self._restarting_since) >= self._recovery_window

    def _check_chronological_order(self, event: CoreEvent) -> None:
        if self._last_event_time is not None and event.timestamp < self._last_event_time:
            raise OutOfOrderEventError(
                f"Events must be processed in chronological order: received "
                f"{event.timestamp} after {self._last_event_time}."
            )
        self._last_event_time = event.timestamp

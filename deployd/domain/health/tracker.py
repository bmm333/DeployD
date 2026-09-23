from collections import deque
from datetime import datetime, timedelta

from deployd.domain.entities.core_event import CoreEvent
from deployd.domain.health.process_health import ProcessHealthFSM
from deployd.domain.health.process_state import ProcessHealthStatus


class ComponentHealthTracker:
    """
    Tracks the health state of components in-memory.
    Maintains a ProcessHealthFSM and a sliding window of recent events per component.
    """

    def __init__(
        self,
        fsm_recovery_window_s: int = 300,
        fsm_max_restarts: int = 3,
        fsm_restart_window_s: int = 120,
        cooldown_window_s: int = 300,
        max_events_per_component: int = 20,
    ) -> None:
        self._fsm_recovery_window_s = fsm_recovery_window_s
        self._fsm_max_restarts = fsm_max_restarts
        self._fsm_restart_window_s = fsm_restart_window_s
        self._cooldown_window = timedelta(seconds=cooldown_window_s)
        self._max_events = max_events_per_component
        # In-memory state
        self._fsm_by_component: dict[str, ProcessHealthFSM] = {}
        self._events_by_component: dict[str, deque[CoreEvent]] = {}
        self._last_investigation_time: dict[str, datetime] = {}

    def process_event(self, event: CoreEvent) -> tuple[bool, ProcessHealthStatus, list[CoreEvent]]:
        """
        Process an incoming event.

        Returns:
            A tuple (should_trigger_investigation, current_fsm_state, recent_events_list)
        """
        component = event.related_component
        if not component:
            # Cannot track health without a related component
            return False, ProcessHealthStatus.HEALTHY, []

        # Enforce a hard limit on tracked components to prevent DoS via OOM
        if component not in self._fsm_by_component and len(self._fsm_by_component) > 1000:
            self._fsm_by_component.clear()
            self._events_by_component.clear()
            self._last_investigation_time.clear()

        if component not in self._fsm_by_component:
            self._fsm_by_component[component] = ProcessHealthFSM(
                recovery_window=timedelta(seconds=self._fsm_recovery_window_s),
                max_restart_count=self._fsm_max_restarts,
                restart_time_window=timedelta(seconds=self._fsm_restart_window_s),
            )
            self._events_by_component[component] = deque(maxlen=self._max_events)

        fsm = self._fsm_by_component[component]
        events_queue = self._events_by_component[component]
        fsm.process_event(event)
        events_queue.append(event)
        current_state = fsm.state
        trigger = False
        if current_state in (ProcessHealthStatus.CRASHING, ProcessHealthStatus.CRASH_LOOP):
            last_time = self._last_investigation_time.get(component)
            event_time = event.timestamp

            if last_time is None or (event_time - last_time) > self._cooldown_window:
                trigger = True
                self._last_investigation_time[component] = event_time

        return trigger, current_state, list(events_queue)

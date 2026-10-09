"""ProcessHealthFSM on events that went through the real HTTP adapter (DID-43)."""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from deployd.adapters.incoming.http_event_adapter import HttpEventAdapter, RawTelemetryEvent
from deployd.domain.entities.core_event import CoreEvent
from deployd.domain.health.process_health import ProcessHealthFSM
from deployd.domain.health.process_state import ProcessHealthStatus
from deployd.domain.health.tracker import ComponentHealthTracker

SCENARIOS = Path("data/scenarios")
H, D = ProcessHealthStatus.HEALTHY, ProcessHealthStatus.DEGRADED

EXPECTED_STATES = {
    "live_degrading_no_trigger": {"auth-service": D},
    "live_novel_analytics_db": {
        "analytics-postgres": H,
        "reporting-service": H,
        "dashboard-ui": D,
    },
    "live_oom_auth_service": {"auth-service": D, "api-gateway": H},
    "live_payment_db_timeout": {
        "postgres-primary": H,
        "payment-service": H,
        "checkout-service": D,
    },
    "live_search_es_cascade": {
        "elasticsearch-cluster": D,
        "search-service": H,
        "api-gateway": D,
    },
}


def _translate(raw: dict[str, Any]) -> CoreEvent:
    return HttpEventAdapter().translate(RawTelemetryEvent(**raw))


def _state(events: list[CoreEvent], component: str) -> ProcessHealthStatus:
    fsm = ProcessHealthFSM(
        recovery_window=timedelta(seconds=300),
        max_restart_count=3,
        restart_time_window=timedelta(seconds=120),
    )
    for event in sorted(events, key=lambda e: e.timestamp):
        if event.related_component == component:
            fsm.process_event(event)
    return fsm.state


@pytest.mark.parametrize("scenario", sorted(EXPECTED_STATES))
def test_live_scenarios_move_the_fsm(scenario: str) -> None:
    events = [_translate(raw) for raw in json.loads((SCENARIOS / f"{scenario}.json").read_text())]

    states = {component: _state(events, component) for component in EXPECTED_STATES[scenario]}

    assert states == EXPECTED_STATES[scenario]


def test_an_oom_kill_over_http_triggers_the_health_tracker() -> None:
    tracker = ComponentHealthTracker()
    oom = _translate(
        {
            "timestamp": "2026-09-30T10:00:00Z",
            "source": "billing-worker",
            "event_type": "OOM_KILL",
            "description": "billing-worker OOM killed",
        }
    )

    triggered, state, _ = tracker.process_event(oom)

    assert triggered
    assert state is ProcessHealthStatus.CRASHING

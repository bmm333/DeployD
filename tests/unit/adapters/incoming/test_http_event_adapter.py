"""Provisional severities the HTTP adapter assigns: the contract ProcessHealthFSM relies on (ADR-006)."""

from __future__ import annotations

from typing import Any

import pytest
from deployd.adapters.incoming.http_event_adapter import HttpEventAdapter, RawTelemetryEvent
from deployd.domain.entities.core_event import CoreEventType, Severity


def _translate(event_type: str, **metadata: Any) -> tuple[CoreEventType, Severity]:
    raw = RawTelemetryEvent(
        timestamp="2026-09-30T10:00:00Z",
        source="auth-service",
        event_type=event_type,
        metadata=metadata,
    )
    event = HttpEventAdapter().translate(raw)
    return event.event_type, event.severity


@pytest.mark.parametrize(
    ("event_type", "metadata", "expected"),
    [
        ("OOM_KILL", {}, (CoreEventType.PROCESS_CRASH, Severity.ERROR)),
        ("SEGFAULT", {}, (CoreEventType.PROCESS_CRASH, Severity.ERROR)),
        ("HEALTHCHECK_FAIL", {}, (CoreEventType.HEALTH_CHECK_FAIL, Severity.ERROR)),
        ("REQUEST_TIMEOUT", {}, (CoreEventType.DEPENDENCY_FAILURE, Severity.WARNING)),
        (
            "MEMORY_SAMPLE",
            {"memory_percent": 97},
            (CoreEventType.RESOURCE_EXHAUSTION, Severity.ERROR),
        ),
        (
            "MEMORY_SAMPLE",
            {"memory_percent": 85},
            (CoreEventType.RESOURCE_EXHAUSTION, Severity.WARNING),
        ),
    ],
)
def test_provisional_severity(
    event_type: str, metadata: dict[str, Any], expected: tuple[CoreEventType, Severity]
) -> None:
    assert _translate(event_type, **metadata) == expected


def test_the_adapter_never_emits_critical() -> None:
    raw_types = ["OOM_KILL", "SEGFAULT", "PROCESS_STOP", "HEALTHCHECK_FAIL", "DEPLOY_FAIL"]
    assert all(_translate(t, memory_percent=99)[1] is not Severity.CRITICAL for t in raw_types)


def test_unknown_raw_types_fall_back_to_state_change() -> None:
    assert _translate("SOMETHING_NEW")[0] is CoreEventType.STATE_CHANGE

"""DID-2 CoreEvent domain invariant tests"""

from datetime import UTC, datetime

import pytest
from deployd.domain.entities.core_event import CoreEvent, CoreEventType, Severity
from pydantic import ValidationError


class TestCoreEventCreation:
    """Valid events -> instatiate without errors"""

    def test_valid_event(self) -> None:
        event = CoreEvent(
            event_type=CoreEventType.PROCESS_CRASH,
            timestamp=datetime.now(tz=UTC),
            severity=Severity.CRITICAL,
            related_component="checkout-api",
            description="checkout-apo terminated unexpectedly",
            metadata={"exit_code": 139, "signal": "SIGSEGV", "pid": 4812},
        )
        assert event.event_type == CoreEventType.PROCESS_CRASH
        assert event.severity == Severity.CRITICAL
        assert event.related_component == "checkout-api"
        assert event.metadata["exit_code"] == 139

    def test_event_id_is_auto_generated(self) -> None:
        e1 = CoreEvent(
            event_type=CoreEventType.DEPLOY_STARTED,
            timestamp=datetime.now(tz=UTC),
            severity=Severity.INFO,
            description="deploy started",
        )
        e2 = CoreEvent(
            event_type=CoreEventType.DEPLOY_STARTED,
            timestamp=datetime.now(tz=UTC),
            severity=Severity.INFO,
            description="deploy started",
        )
        assert e1.event_id != e2.event_id

    def test_component_can_be_absent(self) -> None:
        event = CoreEvent(
            event_type=CoreEventType.HUMAN_OBSERVATION,
            timestamp=datetime.now(tz=UTC),
            severity=Severity.WARNING,
            description="something looks wrong",
        )
        assert event.related_component is None


class TestCoreEventImmutability:
    """Facts do not change."""

    def test_cannot_mutate_fields(self) -> None:
        event = CoreEvent(
            event_type=CoreEventType.PROCESS_CRASH,
            timestamp=datetime.now(tz=UTC),
            severity=Severity.CRITICAL,
            description="crash",
        )
        with pytest.raises(ValidationError):
            event.severity = Severity.INFO  # type: ignore[misc]

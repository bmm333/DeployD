"""DID-2 CoreEvent domain invariant tests"""

from datetime import UTC, datetime, timezone

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
            event.severity = Severity.INFO


class TestCoreEventTimestamp:
    """Domain invariant: timestamps must be timezone-aware UTC."""

    def test_naive_timestamp_is_rejected(self) -> None:
        with pytest.raises(ValidationError, match="timezone-aware"):
            CoreEvent(
                event_type=CoreEventType.STATE_CHANGE,
                timestamp=datetime(2024, 1, 1, 12, 0, 0),  # naive
                severity=Severity.CRITICAL,
                description="state changed",
            )

    def test_non_utc_timestamp_is_converted(self) -> None:
        from datetime import timedelta

        cet = timezone(timedelta(hours=2))
        event = CoreEvent(
            event_type=CoreEventType.STATE_CHANGE,
            timestamp=datetime(2024, 1, 1, 14, 0, 0, tzinfo=cet),
            severity=Severity.CRITICAL,
            description="state changed",
        )
        assert event.timestamp.tzinfo == UTC
        assert event.timestamp.hour == 12


class TestCoreEventSerialization:
    """Metadata must survive serialization round-trips."""

    def test_metadata_preserved(self) -> None:
        meta = {"exit_code": 139, "signal": "SIGSEGV", "pid": 4812}
        event = CoreEvent(
            event_type=CoreEventType.PROCESS_CRASH,
            timestamp=datetime.now(tz=UTC),
            severity=Severity.CRITICAL,
            description="crash",
            metadata=meta,
        )
        dumped = event.model_dump()
        assert dumped["metadata"] == meta

    def test_json_round_trip(self) -> None:
        event = CoreEvent(
            event_type=CoreEventType.DEPLOY_COMPLETED,
            timestamp=datetime.now(tz=UTC),
            severity=Severity.INFO,
            description="deploy done",
        )
        json_str = event.model_dump_json()
        restored = CoreEvent.model_validate_json(json_str)
        assert restored.event_id == event.event_id
        assert restored.event_type == event.event_type


class TestCoreEventTypeRestriction:
    """event_type and severity must be valid enum members."""

    def test_invalid_event_type_rejected(self) -> None:
        with pytest.raises(ValidationError):
            CoreEvent(
                event_type="BANANA",
                timestamp=datetime.now(tz=UTC),
                severity=Severity.INFO,
                description="invalid",
            )

    def test_invalid_severity_rejected(self) -> None:
        with pytest.raises(ValidationError):
            CoreEvent(
                event_type=CoreEventType.STATE_CHANGE,
                timestamp=datetime.now(tz=UTC),
                severity="MEGA_BAD",
                description="invalid",
            )

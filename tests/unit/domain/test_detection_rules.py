"""DID-9 Detection layer tests: DetectionRule protocol conformance + ConfigDriftRule"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from deployd.domain.detection.config_drift_rule import (
    ACTUAL_DEPENDENCY_METADATA_KEY,
    ConfigDriftRule,
)
from deployd.domain.detection.detection_rule import DetectionRule
from deployd.domain.entities.core_event import CoreEvent, CoreEventType, Severity

BASE_TIME = datetime(2026, 8, 18, 12, 0, 0, tzinfo=timezone.utc)


def _make_event(
    *,
    event_type: CoreEventType = CoreEventType.CONFIG_CHANGE,
    severity: Severity = Severity.INFO,
    related_component: str | None = "checkout-api",
    description: str = "config changed",
    timestamp: datetime = BASE_TIME,
    metadata: dict[str, object] | None = None,
) -> CoreEvent:
    return CoreEvent(
        event_id=uuid.uuid4(),
        timestamp=timestamp,
        event_type=event_type,
        severity=severity,
        related_component=related_component,
        description=description,
        metadata=metadata or {},
    )


class TestDetectionRuleProtocol:
    """ConfigDriftRule must structurally satisfy the DetectionRule interface."""

    def test_config_drift_rule_satisfies_protocol(self) -> None:
        rule = ConfigDriftRule()
        assert isinstance(rule, DetectionRule)

    def test_rule_has_rule_id(self) -> None:
        rule = ConfigDriftRule()
        assert rule.rule_id == "CONFIG_DRIFT_RULE"


class TestConfigDriftRuleDetectsDrift:
    """The motivating case: expected_dependency != actual_dependency -> drift."""

    def test_mismatch_emits_config_drift_detected(self) -> None:
        rule = ConfigDriftRule()
        evidence = _make_event(
            related_component="checkout-api",
            metadata={ACTUAL_DEPENDENCY_METADATA_KEY: "test_db"},
        )
        reference = {"checkout-api": "prod_db"}

        emitted = rule.evaluate(evidence, reference)

        assert len(emitted) == 1
        drift = emitted[0]
        assert drift.event_type == CoreEventType.CONFIG_DRIFT_DETECTED
        assert drift.related_component == "checkout-api"
        assert drift.metadata["expected_dependency"] == "prod_db"
        assert drift.metadata["actual_dependency"] == "test_db"
        assert drift.metadata["source_event_id"] == str(evidence.event_id)

    def test_drift_event_timestamp_matches_evidence(self) -> None:
        rule = ConfigDriftRule()
        evidence = _make_event(
            related_component="checkout-api",
            metadata={ACTUAL_DEPENDENCY_METADATA_KEY: "test_db"},
            timestamp=BASE_TIME,
        )
        reference = {"checkout-api": "prod_db"}

        emitted = rule.evaluate(evidence, reference)

        assert emitted[0].timestamp == BASE_TIME


class TestConfigDriftRuleNoDrift:
    """Expected == actual -> nothing emitted."""

    def test_matching_dependency_emits_nothing(self) -> None:
        rule = ConfigDriftRule()
        evidence = _make_event(
            related_component="checkout-api",
            metadata={ACTUAL_DEPENDENCY_METADATA_KEY: "prod_db"},
        )
        reference = {"checkout-api": "prod_db"}

        emitted = rule.evaluate(evidence, reference)

        assert emitted == []


class TestConfigDriftRuleMissingReferenceData:
    """No declared expectation for the component -> the rule refuses to guess."""

    def test_component_absent_from_reference_emits_nothing(self) -> None:
        rule = ConfigDriftRule()
        evidence = _make_event(
            related_component="checkout-api",
            metadata={ACTUAL_DEPENDENCY_METADATA_KEY: "test_db"},
        )
        reference: dict[str, str] = {"payments-api": "payments_db"}

        emitted = rule.evaluate(evidence, reference)

        assert emitted == []

    def test_empty_reference_emits_nothing(self) -> None:
        rule = ConfigDriftRule()
        evidence = _make_event(
            related_component="checkout-api",
            metadata={ACTUAL_DEPENDENCY_METADATA_KEY: "test_db"},
        )

        emitted = rule.evaluate(evidence, {})

        assert emitted == []


class TestConfigDriftRuleMissingEvidence:
    """Rule must not fabricate anything it wasn't given."""

    def test_non_config_change_event_emits_nothing(self) -> None:
        rule = ConfigDriftRule()
        evidence = _make_event(
            event_type=CoreEventType.PROCESS_CRASH,
            related_component="checkout-api",
            metadata={ACTUAL_DEPENDENCY_METADATA_KEY: "test_db"},
        )
        reference = {"checkout-api": "prod_db"}

        emitted = rule.evaluate(evidence, reference)

        assert emitted == []

    def test_missing_related_component_emits_nothing(self) -> None:
        rule = ConfigDriftRule()
        evidence = _make_event(
            related_component=None,
            metadata={ACTUAL_DEPENDENCY_METADATA_KEY: "test_db"},
        )
        reference = {"checkout-api": "prod_db"}

        emitted = rule.evaluate(evidence, reference)

        assert emitted == []

    def test_missing_actual_dependency_metadata_emits_nothing(self) -> None:
        rule = ConfigDriftRule()
        evidence = _make_event(related_component="checkout-api", metadata={})
        reference = {"checkout-api": "prod_db"}

        emitted = rule.evaluate(evidence, reference)

        assert emitted == []

    def test_non_string_actual_dependency_emits_nothing(self) -> None:
        rule = ConfigDriftRule()
        evidence = _make_event(
            related_component="checkout-api",
            metadata={ACTUAL_DEPENDENCY_METADATA_KEY: 123},
        )
        reference = {"checkout-api": "prod_db"}

        emitted = rule.evaluate(evidence, reference)

        assert emitted == []


class TestConfigDriftRuleDoesNotFabricate:
    """The rule only ever reasons about the single component in the evidence."""

    def test_other_reference_components_are_ignored(self) -> None:
        rule = ConfigDriftRule()
        evidence = _make_event(
            related_component="checkout-api",
            metadata={ACTUAL_DEPENDENCY_METADATA_KEY: "test_db"},
        )
        reference = {
            "checkout-api": "prod_db",
            "payments-api": "payments_db",
            "auth-api": "auth_db",
        }

        emitted = rule.evaluate(evidence, reference)

        assert len(emitted) == 1
        assert emitted[0].related_component == "checkout-api"

    def test_emitted_metadata_contains_only_supplied_values(self) -> None:
        rule = ConfigDriftRule()
        evidence = _make_event(
            related_component="checkout-api",
            metadata={ACTUAL_DEPENDENCY_METADATA_KEY: "test_db"},
        )
        reference = {"checkout-api": "prod_db"}

        emitted = rule.evaluate(evidence, reference)

        metadata = emitted[0].metadata
        assert metadata["expected_dependency"] == reference["checkout-api"]
        assert metadata["actual_dependency"] == evidence.metadata[ACTUAL_DEPENDENCY_METADATA_KEY]
        # No component beyond the one named in evidence is ever referenced.
        assert "payments-api" not in str(metadata)

"""
DID-9: ConfigDriftRule - first concrete DetectionRule.

Combines observed CONFIG_CHANGE evidence with external reference data to detect
config drift. See ADR-004 and ADR-003.
"""

from __future__ import annotations

from deployd.domain.entities.core_event import CoreEvent, CoreEventType, Severity

# Metadata key a CONFIG_CHANGE CoreEvent must carry the actual (post-change)
# dependency under, keyed by evidence.related_component.
ACTUAL_DEPENDENCY_METADATA_KEY = "target_dependency"


class ConfigDriftRule:
    """
    Compares a declared expected topology against an observed CONFIG_CHANGE event's
    actual target dependency. Emits CONFIG_DRIFT_DETECTED on mismatch.
    """

    rule_id: str = "CONFIG_DRIFT_RULE"

    def evaluate(self, evidence: CoreEvent, reference: dict[str, str]) -> list[CoreEvent]:
        if evidence.event_type != CoreEventType.CONFIG_CHANGE:
            return []

        component = evidence.related_component
        if component is None:
            return []

        actual_dependency = evidence.metadata.get(ACTUAL_DEPENDENCY_METADATA_KEY)
        if not isinstance(actual_dependency, str):
            return []

        expected_dependency = reference.get(component)
        if expected_dependency is None:
            return []

        if expected_dependency == actual_dependency:
            return []

        drift_event = CoreEvent(
            event_type=CoreEventType.CONFIG_DRIFT_DETECTED,
            timestamp=evidence.timestamp,
            severity=Severity.WARNING,
            related_component=component,
            description=(
                f"{component} is expected to use '{expected_dependency}' but the "
                f"observed CONFIG_CHANGE shows it actually uses '{actual_dependency}'"
            ),
            metadata={
                "expected_dependency": expected_dependency,
                "actual_dependency": actual_dependency,
                "source_event_id": str(evidence.event_id),
                "rule_id": self.rule_id,
            },
        )
        return [drift_event]

"""Pure correlation rule predicates for the DeployD causal inference engine."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from deployd.domain.entities.core_event import CoreEvent, CoreEventType

# Result type


@dataclass(frozen=True)
class RuleMatch:
    """Returned by a rule when it fires."""

    trigger: CoreEvent
    cause: CoreEvent | None
    rule_id: str
    confidence: float = field(default=0.85)


# Type alias

CorrelationRuleFn = Callable[[CoreEvent, Sequence[CoreEvent]], list[RuleMatch]]


_DB_KEYWORDS = frozenset(
    {"db", "database", "postgres", "postgresql", "mysql", "redis", "mongo", "dynamodb", "rds"}
)


def _is_db_source(event: CoreEvent) -> bool:
    source = (event.related_component or "").lower()
    return any(kw in source for kw in _DB_KEYWORDS)


def _latency_value(event: CoreEvent) -> float | None:
    """Extract a numeric latency value from event metadata, if present."""
    for key in ("latency_ms", "duration_ms", "value"):
        val = event.metadata.get(key)
        if isinstance(val, int | float):
            return float(val)
    return None


def _resource_percent(event: CoreEvent) -> float | None:
    """Extract a CPU/memory usage percentage from event metadata, if present."""
    for key in ("percent", "cpu_percent", "memory_percent", "usage_percent"):
        val = event.metadata.get(key)
        if isinstance(val, int | float):
            return float(val)
    return None


def rule_db_latency_anomaly(
    event: CoreEvent,
    window: Sequence[CoreEvent],  # noqa: ARG001 — root rule, no window needed
) -> list[RuleMatch]:
    """RULE-01 — DB Latency Anomaly"""
    if event.event_type is not CoreEventType.STATE_CHANGE:
        return []
    if not _is_db_source(event):
        return []
    latency = _latency_value(event)
    if latency is None or latency < 500:
        return []

    return [
        RuleMatch(trigger=event, cause=None, rule_id="RULE-01-DB-LATENCY-ANOMALY", confidence=0.75)
    ]


def rule_downstream_timeout(
    event: CoreEvent,
    window: Sequence[CoreEvent],
) -> list[RuleMatch]:
    """RULE-02 — Downstream Service Timeout after DB Anomaly"""
    if event.event_type not in (CoreEventType.DEPENDENCY_FAILURE, CoreEventType.CONNECTIVITY_LOSS):
        return []
    if _is_db_source(event):
        return []  # DB timing out itself ->RULE-01 handles it

    # Only fire for explicit timeout / connection error raw types
    raw_type = str(event.metadata.get("_raw_event_type", "")).upper()
    if raw_type not in (
        "REQUEST_TIMEOUT",
        "CONNECTION_ERROR",
        "WORKER_TIMEOUT",
        "HTTP_ERROR",
        "",
    ):
        return []

    # Locate most recent DB latency anomaly in the window
    db_anomalies = [
        e
        for e in window
        if e.event_type is CoreEventType.STATE_CHANGE
        and _is_db_source(e)
        and (_latency_value(e) or 0.0) >= 500
    ]
    if not db_anomalies:
        return []

    cause = max(db_anomalies, key=lambda e: e.timestamp)
    return [
        RuleMatch(
            trigger=event,
            cause=cause,
            rule_id="RULE-02-DOWNSTREAM-TIMEOUT-AFTER-DB",
            confidence=0.80,
        )
    ]


def rule_http_500_cluster(
    event: CoreEvent,
    window: Sequence[CoreEvent],
) -> list[RuleMatch]:
    """RULE-03 — HTTP 500 Cluster following Upstream Failure"""
    if event.event_type is not CoreEventType.STATE_CHANGE:
        return []

    status = event.metadata.get("status_code")
    if not isinstance(status, int) or status < 500:
        return []

    source = event.related_component or ""

    cluster = [
        e
        for e in window
        if e.related_component == source
        and isinstance(e.metadata.get("status_code"), int)
        and e.metadata["status_code"] >= 500  # type: ignore[operator]
    ]
    if len(cluster) < 3:
        return []

    upstream_failures = [
        e
        for e in window
        if e.event_type is CoreEventType.DEPENDENCY_FAILURE and e.related_component != source
    ]
    if not upstream_failures:
        return []

    cause = max(upstream_failures, key=lambda e: e.timestamp)
    return [
        RuleMatch(
            trigger=event,
            cause=cause,
            rule_id="RULE-03-HTTP-500-CLUSTER",
            confidence=0.85,
        )
    ]


def rule_healthcheck_failure_cascade(
    event: CoreEvent,
    window: Sequence[CoreEvent],
) -> list[RuleMatch]:
    """RULE-04 — Health Check Failure following upstream anomaly"""
    if event.event_type is not CoreEventType.HEALTH_CHECK_FAIL:
        return []

    antecedents = [
        e
        for e in window
        if e.related_component != event.related_component
        and (
            e.event_type is CoreEventType.DEPENDENCY_FAILURE
            or (
                e.event_type is CoreEventType.STATE_CHANGE
                and _is_db_source(e)
                and (_latency_value(e) or 0.0) >= 500
            )
        )
    ]
    if not antecedents:
        return []

    cause = max(antecedents, key=lambda e: e.timestamp)
    return [
        RuleMatch(
            trigger=event,
            cause=cause,
            rule_id="RULE-04-HEALTHCHECK-FAILURE-CASCADE",
            confidence=0.78,
        )
    ]


def rule_resource_exhaustion(
    event: CoreEvent,
    window: Sequence[CoreEvent],  # noqa: ARG001 — root rule
) -> list[RuleMatch]:
    """RULE-05 — Resource Exhaustion"""
    if event.event_type is not CoreEventType.RESOURCE_EXHAUSTION:
        return []
    pct = _resource_percent(event)
    if pct is None or pct < 90:
        return []

    return [
        RuleMatch(
            trigger=event,
            cause=None,
            rule_id="RULE-05-RESOURCE-EXHAUSTION",
            confidence=0.90,
        )
    ]


DEFAULT_RULES: Sequence[CorrelationRuleFn] = [
    rule_db_latency_anomaly,
    rule_downstream_timeout,
    rule_http_500_cluster,
    rule_healthcheck_failure_cascade,
    rule_resource_exhaustion,
]

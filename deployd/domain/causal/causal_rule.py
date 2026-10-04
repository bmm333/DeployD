"""Pure correlation rule predicates for the DeployD causal inference engine."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from deployd.domain.causal.config import CorrelationConfig
from deployd.domain.causal.topology import Topology
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
CorrelationRuleFn = Callable[
    [CoreEvent, Sequence[CoreEvent], CorrelationConfig, Topology], list[RuleMatch]
]


def _linked(effect: CoreEvent, cause: CoreEvent, topology: Topology) -> bool:
    """Whether *cause* can propagate to *effect*: same component, a declared call,
    or the effect event names the cause's component as its dependency."""
    caller, callee = effect.related_component, cause.related_component
    if not caller or not callee:
        return False
    return (
        caller == callee
        or topology.calls_component(caller, callee)
        or effect.metadata.get("dependency") == callee
    )


def _is_db_source(event: CoreEvent, config: CorrelationConfig) -> bool:
    source = (event.related_component or "").lower()
    return any(kw in source for kw in config.database_components)


def _latency_value(event: CoreEvent, config: CorrelationConfig) -> float | None:
    """Extract a numeric latency value from event metadata, if present."""
    val = config.get_metric(event.metadata, "latency_ms")
    if isinstance(val, int | float):
        return float(val)
    return None


def _resource_percent(event: CoreEvent, config: CorrelationConfig) -> float | None:
    """Extract a CPU/memory usage percentage from event metadata, if present."""
    val = config.get_metric(event.metadata, "memory_percent")
    if isinstance(val, int | float):
        return float(val)
    return None


def _status_code(event: CoreEvent, config: CorrelationConfig) -> int | None:
    val = config.get_metric(event.metadata, "status_code")
    if isinstance(val, int):
        return val
    return None


def rule_db_latency_anomaly(
    event: CoreEvent,
    window: Sequence[CoreEvent],  # noqa: ARG001 — root rule, no window needed
    config: CorrelationConfig,
    topology: Topology,
) -> list[RuleMatch]:
    """RULE-01 — DB Latency Anomaly"""
    if event.event_type is not CoreEventType.STATE_CHANGE:
        return []
    if not _is_db_source(event, config):
        return []
    latency = _latency_value(event, config)
    if latency is None or latency < config.thresholds.latency_ms:
        return []

    return [
        RuleMatch(trigger=event, cause=None, rule_id="RULE-01-DB-LATENCY-ANOMALY", confidence=0.75)
    ]


def rule_downstream_timeout(
    event: CoreEvent,
    window: Sequence[CoreEvent],
    config: CorrelationConfig,
    topology: Topology,
) -> list[RuleMatch]:
    """RULE-02 — Downstream Service Timeout after DB Anomaly"""
    if event.event_type not in (CoreEventType.DEPENDENCY_FAILURE, CoreEventType.CONNECTIVITY_LOSS):
        return []
    if _is_db_source(event, config):
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

    # Most recent DB latency anomaly or resource exhaustion on a component this one depends on
    antecedents = [
        e
        for e in window
        if _linked(event, e, topology)
        and (
            (
                e.event_type is CoreEventType.STATE_CHANGE
                and _is_db_source(e, config)
                and (_latency_value(e, config) or 0.0) >= config.thresholds.latency_ms
            )
            or (
                e.event_type is CoreEventType.RESOURCE_EXHAUSTION
                and (_resource_percent(e, config) or 0.0) >= config.thresholds.resource_percent
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
            rule_id="RULE-02-DOWNSTREAM-TIMEOUT-AFTER-ANOMALY",
            confidence=0.80,
        )
    ]


def rule_http_500_cluster(
    event: CoreEvent,
    window: Sequence[CoreEvent],
    config: CorrelationConfig,
    topology: Topology,
) -> list[RuleMatch]:
    """RULE-03 — HTTP 500 Cluster following Upstream Failure"""
    if event.event_type is not CoreEventType.STATE_CHANGE:
        return []

    status = config.get_metric(event.metadata, "status_code")
    if not isinstance(status, int) or status < 500:
        return []

    source = event.related_component or ""

    cluster = [
        e for e in window if e.related_component == source and (_status_code(e, config) or 0) >= 500
    ]
    if len(cluster) < config.thresholds.error_rate:
        return []

    upstream_failures = [
        e
        for e in window
        if e.event_type is CoreEventType.DEPENDENCY_FAILURE and _linked(event, e, topology)
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
    config: CorrelationConfig,
    topology: Topology,
) -> list[RuleMatch]:
    """RULE-04 — Health Check Failure following upstream anomaly"""
    if event.event_type is not CoreEventType.HEALTH_CHECK_FAIL:
        return []

    antecedents = [
        e
        for e in window
        if e.related_component != event.related_component
        and _linked(event, e, topology)
        and (
            e.event_type is CoreEventType.DEPENDENCY_FAILURE
            or (
                e.event_type is CoreEventType.STATE_CHANGE
                and _is_db_source(e, config)
                and (_latency_value(e, config) or 0.0) >= config.thresholds.latency_ms
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
    window: Sequence[CoreEvent],
    config: CorrelationConfig,
    topology: Topology,
) -> list[RuleMatch]:
    """RULE-05 — Resource Exhaustion"""
    if event.event_type is not CoreEventType.RESOURCE_EXHAUSTION:
        return []
    pct = _resource_percent(event, config)
    if pct is None or pct < config.thresholds.resource_percent:
        return []

    # Check if there is a config drift that might have caused it
    config_drifts = [
        e
        for e in window
        if e.event_type is CoreEventType.STATE_CHANGE
        and _linked(event, e, topology)
        and ("config" in (e.related_component or "").lower() or "config" in e.description.lower())
    ]
    cause = max(config_drifts, key=lambda e: e.timestamp) if config_drifts else None

    return [
        RuleMatch(
            trigger=event,
            cause=cause,
            rule_id="RULE-05-RESOURCE-EXHAUSTION",
            confidence=0.90,
        )
    ]


def rule_config_drift(
    event: CoreEvent,
    window: Sequence[CoreEvent],
    config: CorrelationConfig,
    topology: Topology,
) -> list[RuleMatch]:
    """RULE-06 — Config Drift Anomaly"""
    if event.event_type is not CoreEventType.STATE_CHANGE:
        return []
    if (
        "config" not in (event.related_component or "").lower()
        and "config" not in event.description.lower()
    ):
        return []

    return [
        RuleMatch(
            trigger=event,
            cause=None,
            rule_id="RULE-06-CONFIG-DRIFT",
            confidence=0.95,
        )
    ]


DEFAULT_RULES: Sequence[CorrelationRuleFn] = [
    rule_db_latency_anomaly,
    rule_downstream_timeout,
    rule_http_500_cluster,
    rule_healthcheck_failure_cascade,
    rule_resource_exhaustion,
    rule_config_drift,
]

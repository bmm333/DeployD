"""Correlation rules only link cause and effect when the topology connects them."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from deployd.domain.causal.causal_rule import (
    rule_db_latency_anomaly,
    rule_downstream_timeout,
    rule_healthcheck_failure_cascade,
    rule_http_500_cluster,
    rule_resource_exhaustion,
    rule_config_drift,
)
from deployd.domain.causal.config import CorrelationConfig
from deployd.domain.causal.topology import Topology
from deployd.domain.entities.core_event import CoreEvent, CoreEventType, Severity

T0 = datetime(2026, 9, 30, 10, 0, tzinfo=timezone.utc)
CONFIG = CorrelationConfig(
    database_components=["postgres"],
    metric_field_aliases={
        "latency_ms": ["latency_ms"],
        "memory_percent": ["memory_percent", "cpu_percent"],
        "status_code": ["status_code"],
    },
)
TOPOLOGY = Topology.from_calls(
    {
        "payment-service": ["postgres-primary"],
        "checkout-service": ["payment-service"],
        "api-gateway": ["auth-service"],
    }
)
NO_TOPOLOGY = Topology()


def _ev(
    component: str,
    event_type: CoreEventType,
    second: int = 0,
    description: str = "",
    **metadata: object,
) -> CoreEvent:
    return CoreEvent(
        event_type=event_type,
        severity=Severity.ERROR,
        timestamp=T0 + timedelta(seconds=second),
        related_component=component,
        description=description or f"{event_type.value} on {component}",
        metadata=metadata,
    )


# ── RULE-02: downstream timeout after a dependency anomaly ────────────────────

DB_SLOW = _ev("postgres-primary", CoreEventType.STATE_CHANGE, 0, latency_ms=3200)
OTHER_DB_SLOW = _ev("analytics-postgres", CoreEventType.STATE_CHANGE, 5, latency_ms=4200)


def test_rule02_links_timeout_to_an_anomaly_of_a_called_dependency() -> None:
    timeout = _ev("payment-service", CoreEventType.DEPENDENCY_FAILURE, 20)

    [match] = rule_downstream_timeout(timeout, [DB_SLOW, timeout], CONFIG, TOPOLOGY)

    assert match.cause == DB_SLOW


def test_rule02_ignores_a_more_recent_anomaly_on_an_unrelated_database() -> None:
    timeout = _ev("payment-service", CoreEventType.DEPENDENCY_FAILURE, 20)

    [match] = rule_downstream_timeout(timeout, [DB_SLOW, OTHER_DB_SLOW, timeout], CONFIG, TOPOLOGY)

    assert match.cause == DB_SLOW


def test_rule02_without_any_link_produces_no_edge() -> None:
    timeout = _ev("shipping-service", CoreEventType.DEPENDENCY_FAILURE, 20)

    assert rule_downstream_timeout(timeout, [DB_SLOW, timeout], CONFIG, TOPOLOGY) == []


def test_rule02_accepts_a_dependency_named_by_the_event_itself() -> None:
    hot = _ev("auth-service", CoreEventType.RESOURCE_EXHAUSTION, 0, memory_percent=97)
    timeout = _ev("api-gateway", CoreEventType.DEPENDENCY_FAILURE, 20, dependency="auth-service")

    [match] = rule_downstream_timeout(timeout, [hot, timeout], CONFIG, NO_TOPOLOGY)

    assert match.cause == hot


# ── RULE-03: HTTP 500 cluster after an upstream failure ───────────────────────


def _cluster(component: str) -> list[CoreEvent]:
    return [_ev(component, CoreEventType.STATE_CHANGE, 30 + i, status_code=500) for i in range(3)]


def test_rule03_links_500s_to_a_failure_of_a_called_component() -> None:
    failure = _ev("auth-service", CoreEventType.DEPENDENCY_FAILURE, 10)
    errors = _cluster("api-gateway")

    [match] = rule_http_500_cluster(errors[-1], [failure, *errors], CONFIG, TOPOLOGY)

    assert match.cause == failure


def test_rule03_ignores_failures_of_unrelated_components() -> None:
    failure = _ev("shipping-service", CoreEventType.DEPENDENCY_FAILURE, 10)
    errors = _cluster("api-gateway")

    assert rule_http_500_cluster(errors[-1], [failure, *errors], CONFIG, TOPOLOGY) == []


# ── RULE-04: health-check failure after an upstream anomaly ───────────────────


def test_rule04_links_health_check_to_a_called_component_failure() -> None:
    failure = _ev("payment-service", CoreEventType.DEPENDENCY_FAILURE, 10)
    health = _ev("checkout-service", CoreEventType.HEALTH_CHECK_FAIL, 20)

    [match] = rule_healthcheck_failure_cascade(health, [failure, health], CONFIG, TOPOLOGY)

    assert match.cause == failure


def test_rule04_does_not_blame_a_caller_for_the_callee_health_check() -> None:
    # api-gateway calls auth-service, not the other way round.
    gateway_timeout = _ev("api-gateway", CoreEventType.DEPENDENCY_FAILURE, 10)
    health = _ev("auth-service", CoreEventType.HEALTH_CHECK_FAIL, 20)

    assert (
        rule_healthcheck_failure_cascade(health, [gateway_timeout, health], CONFIG, TOPOLOGY) == []
    )


# ── RULE-05: resource exhaustion after a config drift ─────────────────────────


def test_rule05_links_exhaustion_to_a_config_drift_on_the_same_component() -> None:
    drift = _ev("auth-service", CoreEventType.STATE_CHANGE, 0, "config reload: cache size")
    hot = _ev("auth-service", CoreEventType.RESOURCE_EXHAUSTION, 60, memory_percent=97)

    [match] = rule_resource_exhaustion(hot, [drift, hot], CONFIG, TOPOLOGY)

    assert match.cause == drift


def test_rule05_unrelated_config_drift_leaves_the_exhaustion_as_a_root() -> None:
    drift = _ev("billing-batch", CoreEventType.STATE_CHANGE, 0, "config reload: batch size")
    hot = _ev("auth-service", CoreEventType.RESOURCE_EXHAUSTION, 60, memory_percent=97)

    [match] = rule_resource_exhaustion(hot, [drift, hot], CONFIG, TOPOLOGY)

    assert match.cause is None


# ── RULE-01: DB Latency Anomaly ───────────────────────────────────────────────

def test_rule01_positive() -> None:
    event = _ev("postgres-primary", CoreEventType.STATE_CHANGE, 0, latency_ms=600)
    [match] = rule_db_latency_anomaly(event, [], CONFIG, TOPOLOGY)
    assert match.cause is None
    assert match.rule_id == "RULE-01-DB-LATENCY-ANOMALY"


def test_rule01_negative_not_db() -> None:
    event = _ev("auth-service", CoreEventType.STATE_CHANGE, 0, latency_ms=600)
    assert rule_db_latency_anomaly(event, [], CONFIG, TOPOLOGY) == []


def test_rule01_negative_below_threshold() -> None:
    event = _ev("postgres-primary", CoreEventType.STATE_CHANGE, 0, latency_ms=499)
    assert rule_db_latency_anomaly(event, [], CONFIG, TOPOLOGY) == []


def test_rule01_boundary_threshold() -> None:
    event = _ev("postgres-primary", CoreEventType.STATE_CHANGE, 0, latency_ms=500)
    [match] = rule_db_latency_anomaly(event, [], CONFIG, TOPOLOGY)
    assert match.rule_id == "RULE-01-DB-LATENCY-ANOMALY"


# ── RULE-06: Config Drift ─────────────────────────────────────────────────────

def test_rule06_positive_component_name() -> None:
    event = _ev("config-server", CoreEventType.STATE_CHANGE, 0, "Updated")
    [match] = rule_config_drift(event, [], CONFIG, TOPOLOGY)
    assert match.rule_id == "RULE-06-CONFIG-DRIFT"


def test_rule06_positive_description() -> None:
    event = _ev("auth-service", CoreEventType.STATE_CHANGE, 0, "Config changed")
    [match] = rule_config_drift(event, [], CONFIG, TOPOLOGY)
    assert match.rule_id == "RULE-06-CONFIG-DRIFT"


def test_rule06_negative() -> None:
    event = _ev("auth-service", CoreEventType.STATE_CHANGE, 0, "Restarted")
    assert rule_config_drift(event, [], CONFIG, TOPOLOGY) == []


# ── CorrelationConfig & Metric Extraction ─────────────────────────────────────

def test_config_get_metric_resolves_alias() -> None:
    assert CONFIG.get_metric({"cpu_percent": 95}, "memory_percent") == 95


def test_config_get_metric_missing() -> None:
    assert CONFIG.get_metric({"other_metric": 95}, "memory_percent") is None

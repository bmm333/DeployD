"""
deployd/domain/causal/causal_rule.py

Pure correlation rule predicates for the DeployD causal inference engine.

Rules are stateless pure functions with the signature:

    CorrelationRuleFn = Callable[[CoreEvent, Sequence[CoreEvent]], list[RuleMatch]]

- ``event``  : the newly ingested event being evaluated.
- ``window`` : a snapshot of all events currently in the observation window,
               **already time-filtered** by the infrastructure layer.
               Rules never call ``datetime.now()``.

This purity guarantee means every rule can be unit-tested with simple fixtures —
no mocking of clocks, no databases, no I/O.

Rule catalogue
--------------
RULE-01  DB Latency Anomaly       — root node, no causal antecedent
RULE-02  Downstream Timeout       — CAUSAL edge: DB → failing service
RULE-03  HTTP 500 Cluster         — CAUSAL edge: upstream failure → gateway
RULE-04  Healthcheck Cascade      — CAUSAL edge: anomaly → health-check failure
RULE-05  Resource Exhaustion      — root node, no causal antecedent

Extending rules
---------------
Add a new function with the same signature and append it to ``DEFAULT_RULES``.
No other file needs to change.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from deployd.domain.entities.core_event import CoreEvent, CoreEventType

# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RuleMatch:
    """
    Returned by a rule when it fires.

    trigger    : The incoming event that caused the rule to fire.
    cause      : The antecedent event from the window that is the causal
                 predecessor. ``None`` for root-anomaly rules (RULE-01, RULE-05)
                 that have no known upstream cause.
    rule_id    : Stable, human-readable identifier used for edge annotation and
                 audit trails.
    confidence : 0.0–1.0 probability that the causal link is real.
                 Conservative values are preferred; the graph topology provides
                 the additional signal for severity escalation.
    """

    trigger: CoreEvent
    cause: CoreEvent | None
    rule_id: str
    confidence: float = field(default=0.85)


# ---------------------------------------------------------------------------
# Type alias
# ---------------------------------------------------------------------------

CorrelationRuleFn = Callable[[CoreEvent, Sequence[CoreEvent]], list[RuleMatch]]

# ---------------------------------------------------------------------------
# Helper predicates (private)
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Rule implementations
# ---------------------------------------------------------------------------


def rule_db_latency_anomaly(
    event: CoreEvent,
    window: Sequence[CoreEvent],  # noqa: ARG001 — root rule, no window needed
) -> list[RuleMatch]:
    """
    RULE-01 — DB Latency Anomaly

    Condition
    ---------
    A ``STATE_CHANGE`` event from a database-like service with ``latency_ms``
    (or ``value``) >= 500 ms.

    Action
    ------
    Root anomaly node — no causal antecedent.  This node may become the
    antecedent for RULE-02 and RULE-03 once downstream failures arrive.

    Confidence: 0.75
        A single high-latency query is observational noise.  Confidence
        intentionally low so a solitary node does not raise the incident
        severity on its own.
    """
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
    """
    RULE-02 — Downstream Service Timeout after DB Anomaly

    Condition
    ---------
    A ``DEPENDENCY_FAILURE`` or ``CONNECTIVITY_LOSS`` event from a **non-DB**
    service, and the window contains at least one RULE-01 match (DB latency
    event >= 500 ms).

    Action
    ------
    CAUSAL edge: db_anomaly_event → this_timeout_event.
    Links to the most recent DB latency anomaly in the window.

    Confidence: 0.80
        Temporal correlation, not proven causality.
    """
    if event.event_type not in (CoreEventType.DEPENDENCY_FAILURE, CoreEventType.CONNECTIVITY_LOSS):
        return []
    if _is_db_source(event):
        return []  # DB timing out itself → RULE-01 handles it

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
    """
    RULE-03 — HTTP 500 Cluster following Upstream Failure

    Condition
    ---------
    1. Incoming event is a ``STATE_CHANGE`` with ``status_code >= 500``.
    2. At least 3 events with ``status_code >= 500`` from the **same source**
       exist in the window (includes the current event).
    3. At least one ``DEPENDENCY_FAILURE`` from a **different** service exists
       in the window.

    Action
    ------
    CAUSAL edge: most-recent upstream_failure → this event.
    A solitary 500 is noise; a cluster following upstream failures is a symptom
    of a cascading incident.

    Confidence: 0.85
    """
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
    """
    RULE-04 — Health Check Failure following upstream anomaly

    Condition
    ---------
    A ``HEALTH_CHECK_FAIL`` event, and the window contains at least one
    ``DEPENDENCY_FAILURE`` or a DB latency anomaly from a **different** service.

    Action
    ------
    CAUSAL edge: most-recent antecedent → this healthcheck failure.

    Confidence: 0.78
    """
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
    """
    RULE-05 — Resource Exhaustion

    Condition
    ---------
    A ``RESOURCE_EXHAUSTION`` event with CPU or memory usage >= 90%.

    Action
    ------
    Root anomaly node — no causal antecedent.  High-utilisation hosts become
    upstream candidates for RULE-02 and RULE-03.

    Confidence: 0.90
    """
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


# ---------------------------------------------------------------------------
# Default rule set — evaluated in order on every ingested event
# ---------------------------------------------------------------------------

DEFAULT_RULES: Sequence[CorrelationRuleFn] = [
    rule_db_latency_anomaly,
    rule_downstream_timeout,
    rule_http_500_cluster,
    rule_healthcheck_failure_cascade,
    rule_resource_exhaustion,
]

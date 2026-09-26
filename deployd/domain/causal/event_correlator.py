"""
EventCorrelator — sliding-window causal correlation engine.

Responsibility
--------------
The correlator maintains a 5-minute (configurable) sliding window of ingested
``CoreEvent`` objects and applies a declarative rule set to determine when a
causal relationship between events justifies adding a node and/or a CAUSAL
edge to the ``IncidentGraph``.

Design principles
-----------------
1. **No severity label from the outside.** Severity is an emergent property of
   the graph topology — the depth and fan-out of the causal chain determine
   how serious the incident is. A single latency spike is INFO. The same spike
   followed by three downstream timeouts, two HTTP 500 clusters, and a queue
   overflow is CRITICAL.

2. **Rules are declarative.** Each ``CorrelationRule`` is a pure predicate over
   the sliding window. Adding a new rule never touches existing rules.

3. **Idempotent graph writes.** The correlator catches ``DuplicateNodeError``
   and ``DuplicateEdgeError`` from the graph and silently skips them. Running
   the same event batch twice produces the same graph.

4. **Swap-ready.** The sliding window is backed by a plain Python ``deque``.
   Swapping it for a Redis Streams consumer requires only replacing
   ``_SlidingWindow`` — the correlator logic is unchanged.

Public surface
--------------
::

    correlator = EventCorrelator(graph)
    correlator.ingest(core_event)          # call once per received event
    graph_state = correlator.graph         # read the accumulated IncidentGraph

"""

from __future__ import annotations

import logging
from collections import deque
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from deployd.domain.entities.core_event import CoreEvent, CoreEventType
from deployd.domain.graph.edge import GraphEdge
from deployd.domain.graph.edge_type import EdgeType
from deployd.domain.graph.graph import DuplicateEdgeError, DuplicateNodeError, IncidentGraph
from deployd.domain.graph.node import GraphNode

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Sliding window
# ---------------------------------------------------------------------------

DEFAULT_WINDOW_SECONDS = 5 * 60  # 5 minutes — configurable at construction time


class _SlidingWindow:
    """
    A time-bounded deque of ``CoreEvent`` objects.

    Expired events (older than ``window_seconds`` from *now*) are pruned on
    every ``append`` call. This keeps memory usage O(events_in_window).

    In production, replace this class body with a Redis Streams consumer that
    reads from an XRANGE with a time-bounded query. The public interface
    (``append``, ``events_within``) stays identical.
    """

    def __init__(self, window_seconds: float = DEFAULT_WINDOW_SECONDS) -> None:
        self._window = timedelta(seconds=window_seconds)
        self._deque: deque[CoreEvent] = deque()

    def append(self, event: CoreEvent) -> None:
        self._deque.append(event)
        self._prune()

    def events_within(self, seconds: float | None = None) -> list[CoreEvent]:
        """Return all events in the window, or events within the last *seconds*."""
        if seconds is None:
            return list(self._deque)
        cutoff = datetime.now(timezone.utc) - timedelta(seconds=seconds)
        return [e for e in self._deque if e.timestamp >= cutoff]

    def _prune(self) -> None:
        cutoff = datetime.now(timezone.utc) - self._window
        while self._deque and self._deque[0].timestamp < cutoff:
            self._deque.popleft()

    def __len__(self) -> int:
        return len(self._deque)


# ---------------------------------------------------------------------------
# Rule contract
# ---------------------------------------------------------------------------


@dataclass
class RuleMatch:
    """
    Returned by a rule when it fires.

    trigger  : The event that caused the rule to fire.
    cause    : The antecedent event to draw a CAUSAL edge *from* (may be None
               for root anomaly rules that add a node with no incoming edge).
    rule_id  : Stable rule identifier for audit / edge annotation.
    confidence : 0.0 – 1.0 confidence in the causal link.
    """

    trigger: CoreEvent
    cause: CoreEvent | None
    rule_id: str
    confidence: float = 0.85


CorrelationRuleFn = Callable[[CoreEvent, _SlidingWindow], list[RuleMatch]]


# ---------------------------------------------------------------------------
# Built-in correlation rules
# ---------------------------------------------------------------------------
# Each rule is a plain function:
#   (incoming_event, window) -> list[RuleMatch]
#
# An empty list means the rule did not fire for this event.
# Rules are evaluated in order; all matches from all rules are applied.
# ---------------------------------------------------------------------------


def _rule_db_latency_anomaly(event: CoreEvent, window: _SlidingWindow) -> list[RuleMatch]:
    """
    RULE-01 — DB Latency Anomaly

    Fires when a latency observation from a database-like service exceeds 500 ms.
    This is a ROOT anomaly — it has no cause node, it IS the potential cause.

    Rationale: a single high-latency query is noise. But this rule adds the node
    so that downstream rules can reference it. The graph's causal chain depth
    determines the actual severity.
    """
    if event.event_type != CoreEventType.STATE_CHANGE:
        return []

    source = (event.related_component or "").lower()
    is_db = any(
        kw in source
        for kw in ("db", "database", "postgres", "mysql", "redis", "mongo", "dynamo", "rds")
    )
    if not is_db:
        return []

    latency = event.metadata.get("latency_ms") or event.metadata.get("value")
    if not isinstance(latency, int | float) or latency < 500:
        return []

    return [
        RuleMatch(trigger=event, cause=None, rule_id="RULE-01-DB-LATENCY-ANOMALY", confidence=0.75)
    ]


def _rule_dependency_timeout_after_db_anomaly(
    event: CoreEvent, window: _SlidingWindow
) -> list[RuleMatch]:
    """
    RULE-02 — Dependency Timeout downstream of DB Latency

    Fires when a REQUEST_TIMEOUT or DEPENDENCY_FAILURE arrives from a non-DB
    service and a DB latency anomaly has been seen in the last 5 minutes.

    Creates a CAUSAL edge: db_anomaly_event → this_timeout_event.

    Confidence 0.80 — temporal correlation, not proven causality.
    """
    if event.event_type not in (CoreEventType.DEPENDENCY_FAILURE, CoreEventType.CONNECTIVITY_LOSS):
        return []

    source = (event.related_component or "").lower()
    is_db = any(kw in source for kw in ("db", "database", "postgres", "mysql", "redis", "mongo"))
    if is_db:
        return []  # DB timing out itself is handled by RULE-01

    raw_type = str(event.metadata.get("_raw_event_type", "")).upper()
    if raw_type not in ("REQUEST_TIMEOUT", "CONNECTION_ERROR", "WORKER_TIMEOUT", "HTTP_ERROR"):
        return []

    # Look for a recent DB latency anomaly in the window
    db_anomalies = [
        e
        for e in window.events_within()
        if e.event_type == CoreEventType.STATE_CHANGE
        and any(
            kw in (e.related_component or "").lower()
            for kw in ("db", "database", "postgres", "mysql", "redis", "mongo", "dynamo", "rds")
        )
        and isinstance(e.metadata.get("latency_ms") or e.metadata.get("value"), int | float)
        and float(e.metadata.get("latency_ms") or e.metadata.get("value") or 0) >= 500  # type: ignore[arg-type]
    ]

    if not db_anomalies:
        return []

    # Link to the most recent DB anomaly
    cause = max(db_anomalies, key=lambda e: e.timestamp)
    return [
        RuleMatch(
            trigger=event,
            cause=cause,
            rule_id="RULE-02-TIMEOUT-AFTER-DB-ANOMALY",
            confidence=0.80,
        )
    ]


def _rule_http_500_cluster(event: CoreEvent, window: _SlidingWindow) -> list[RuleMatch]:
    """
    RULE-03 — HTTP 500 Cluster

    Fires when three or more HTTP 500 events from the same source arrive within
    the last 2 minutes, AND there is already a DEPENDENCY_FAILURE node from a
    different (upstream) service in the window.

    Creates a CAUSAL edge: upstream_failure → this_500_cluster.

    Rationale: a single 500 is noise. A cluster of 500s following upstream
    failures is a symptom of a cascading incident.
    """
    if event.event_type != CoreEventType.STATE_CHANGE:
        return []

    status = event.metadata.get("status_code")
    if not isinstance(status, int) or status < 500:
        return []

    source = event.related_component or ""

    # Count recent 500s from the same source within 2 minutes
    recent_500s = [
        e
        for e in window.events_within(seconds=120)
        if e.related_component == source
        and isinstance(e.metadata.get("status_code"), int)
        and e.metadata["status_code"] >= 500  # type: ignore[operator]
    ]

    if len(recent_500s) < 3:
        return []

    # Look for an upstream DEPENDENCY_FAILURE from a different service
    upstream_failures = [
        e
        for e in window.events_within(seconds=300)
        if e.event_type == CoreEventType.DEPENDENCY_FAILURE and e.related_component != source
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


def _rule_health_check_failure_cascade(event: CoreEvent, window: _SlidingWindow) -> list[RuleMatch]:
    """
    RULE-04 — Health Check Failure following upstream anomaly

    Fires when a HEALTHCHECK_FAIL arrives and there is already a DEPENDENCY_FAILURE
    or a DB latency anomaly in the window.
    """
    if event.event_type != CoreEventType.HEALTH_CHECK_FAIL:
        return []

    antecedents = [
        e
        for e in window.events_within(seconds=300)
        if e.event_type in (CoreEventType.DEPENDENCY_FAILURE, CoreEventType.STATE_CHANGE)
        and e.related_component != event.related_component
        and (
            e.event_type == CoreEventType.DEPENDENCY_FAILURE
            or (
                isinstance(e.metadata.get("latency_ms") or e.metadata.get("value"), int | float)
                and float(e.metadata.get("latency_ms") or e.metadata.get("value") or 0) >= 500  # type: ignore[arg-type]
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
            rule_id="RULE-04-HEALTHCHECK-FAIL-CASCADE",
            confidence=0.78,
        )
    ]


def _rule_resource_exhaustion(event: CoreEvent, window: _SlidingWindow) -> list[RuleMatch]:
    """
    RULE-05 — Resource Exhaustion

    Fires when CPU or memory exceeds 90% on any host. Root anomaly — no cause
    needed. This node may become the antecedent for other rules.
    """
    if event.event_type != CoreEventType.RESOURCE_EXHAUSTION:
        return []

    pct = (
        event.metadata.get("percent")
        or event.metadata.get("cpu_percent")
        or event.metadata.get("memory_percent")
    )
    if not isinstance(pct, int | float) or pct < 90:
        return []

    return [
        RuleMatch(trigger=event, cause=None, rule_id="RULE-05-RESOURCE-EXHAUSTION", confidence=0.90)
    ]


# ---------------------------------------------------------------------------
# Default rule set (ordered by specificity — most specific first)
# ---------------------------------------------------------------------------

DEFAULT_RULES: Sequence[CorrelationRuleFn] = [
    _rule_db_latency_anomaly,
    _rule_dependency_timeout_after_db_anomaly,
    _rule_http_500_cluster,
    _rule_health_check_failure_cascade,
    _rule_resource_exhaustion,
]


# ---------------------------------------------------------------------------
# EventCorrelator
# ---------------------------------------------------------------------------


class EventCorrelator:
    """
    Sliding-window causal correlation engine.

    Parameters
    ----------
    graph
        The ``IncidentGraph`` to write nodes and edges into.
    window_seconds
        Size of the sliding window in seconds. Default: 300 (5 minutes).
    rules
        Ordered sequence of rule functions to evaluate on each ingested event.
        Defaults to ``DEFAULT_RULES``. Pass a custom list to extend or override.

    Usage
    -----
    ::

        graph = IncidentGraph()
        correlator = EventCorrelator(graph)

        for raw_core_event in stream:
            correlator.ingest(raw_core_event)

        # After ingestion, the graph contains nodes + CAUSAL edges.
        for node in graph.nodes:
            print(node.event.related_component, node.event.severity)
    """

    def __init__(
        self,
        graph: IncidentGraph,
        window_seconds: float = DEFAULT_WINDOW_SECONDS,
        rules: Sequence[CorrelationRuleFn] | None = None,
    ) -> None:
        self._graph = graph
        self._window = _SlidingWindow(window_seconds)
        self._rules: Sequence[CorrelationRuleFn] = rules if rules is not None else DEFAULT_RULES
        # Index: event_id → GraphNode, for edge wiring
        self._node_index: dict[str, GraphNode] = {}

    @property
    def graph(self) -> IncidentGraph:
        return self._graph

    def ingest(self, event: CoreEvent) -> None:
        """
        Ingest a single ``CoreEvent`` into the correlator.

        The event is added to the sliding window. All rules are evaluated.
        For each rule that fires, the trigger node is added to the graph (if
        it does not already exist), and a CAUSAL edge is drawn from the cause
        node (if present and already in the graph).
        """
        self._window.append(event)

        for rule_fn in self._rules:
            matches = rule_fn(event, self._window)
            for match in matches:
                self._apply_match(match)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _apply_match(self, match: RuleMatch) -> None:
        trigger_node = self._ensure_node(match.trigger)

        if match.cause is None:
            # Root anomaly — node only, no edge
            return

        cause_node = self._node_index.get(str(match.cause.event_id))
        if cause_node is None:
            # The cause event is in the window but not yet a node — add it
            cause_node = self._ensure_node(match.cause)

        self._ensure_edge(
            source=cause_node,
            target=trigger_node,
            rule_id=match.rule_id,
            confidence=match.confidence,
        )

    def _ensure_node(self, event: CoreEvent) -> GraphNode:
        key = str(event.event_id)
        if key in self._node_index:
            return self._node_index[key]

        node = GraphNode(event=event)
        try:
            self._graph.add_node(node)
            self._node_index[key] = node
            log.info(
                "Graph node added: source=%s type=%s severity=%s rule_candidate",
                event.related_component,
                event.event_type.value,
                event.severity.value,
            )
        except DuplicateNodeError:
            # Already in graph from a previous rule in the same tick
            self._node_index[key] = node

        return node

    def _ensure_edge(
        self,
        source: GraphNode,
        target: GraphNode,
        rule_id: str,
        confidence: float,
    ) -> None:
        edge = GraphEdge(
            source=source.node_id,
            target=target.node_id,
            edge_type=EdgeType.CAUSAL,
            confidence=confidence,
            rule_id=rule_id,
        )
        try:
            self._graph.add_edge(edge)
            log.info(
                "CAUSAL edge: %s → %s (rule=%s, conf=%.2f)",
                source.event.related_component,
                target.event.related_component,
                rule_id,
                confidence,
            )
        except DuplicateEdgeError:
            pass  # Idempotent: same rule fired again for the same pair


# ---------------------------------------------------------------------------
# Convenience factory
# ---------------------------------------------------------------------------


def make_correlator(
    graph: IncidentGraph | None = None,
    window_seconds: float = DEFAULT_WINDOW_SECONDS,
) -> EventCorrelator:
    """Return a ready-to-use ``EventCorrelator`` with an optional pre-existing graph."""
    return EventCorrelator(graph=graph or IncidentGraph(), window_seconds=window_seconds)

"""Correlate a raw CoreEvent against the observation window."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Literal

from deployd.application.ports.event_window_port import EventWindowPort
from deployd.domain.causal.causal_rule import DEFAULT_RULES, CorrelationRuleFn, RuleMatch
from deployd.domain.causal.config import CorrelationConfig
from deployd.domain.entities.core_event import CoreEvent
from deployd.domain.graph.edge import GraphEdge
from deployd.domain.graph.edge_type import EdgeType
from deployd.domain.graph.graph import DuplicateEdgeError, DuplicateNodeError, IncidentGraph
from deployd.domain.graph.node import GraphNode

log = logging.getLogger(__name__)

IncidentSeverity = Literal["Healthy", "Degrading", "Critical"]

CRITICAL_MIN_HOPS = 2


class CorrelateEventsUseCase:
    """Streaming event correlation use case."""

    def __init__(
        self,
        graph: IncidentGraph,
        event_window: EventWindowPort,
        config: CorrelationConfig,
        rules: Sequence[CorrelationRuleFn] | None = None,
    ) -> None:
        self._graph = graph
        self._window = event_window
        self._config = config
        self._rules: Sequence[CorrelationRuleFn] = rules if rules is not None else DEFAULT_RULES
        # Internal index: event_id (str) ->GraphNode, for edge wiring.
        # Avoids a second graph lookup on every edge creation.
        self._node_index: dict[str, GraphNode] = {}

    @property
    def graph(self) -> IncidentGraph:
        return self._graph

    def ingest(self, event: CoreEvent) -> None:
        """
        Ingest a single ``CoreEvent``.

        Flow
        ----
        1. Append to window .
        2. Take a snapshot of the current window contents.
        3. Evaluate every rule against (event, snapshot).
        4. Apply each ``RuleMatch`` to the graph.
        """
        self._window.append(event)
        snapshot = self._window.snapshot()

        for rule_fn in self._rules:
            matches = rule_fn(event, snapshot, self._config)
            for match in matches:
                self._apply_match(match)

    def _apply_match(self, match: RuleMatch) -> None:
        trigger_node = self._ensure_node(match.trigger)

        if match.cause is None:
            # Root anomaly — node only, no incoming edge.
            return

        cause_node = self._node_index.get(str(match.cause.event_id))
        if cause_node is None:
            # Cause is in the window snapshot but not yet in the graph.
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
            log.info(
                "Graph node added: source=%s type=%s severity=%s",
                event.related_component,
                event.event_type.value,
                event.severity.value,
            )
        except DuplicateNodeError:
            pass  # Added by a concurrent rule tick — idempotent
        finally:
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
                "CAUSAL edge: %s ->%s  rule=%s conf=%.2f",
                source.event.related_component,
                target.event.related_component,
                rule_id,
                confidence,
            )
        except DuplicateEdgeError:
            pass  # Idempotent: same rule re-fired for the same node pair


def compute_incident_severity(graph: IncidentGraph) -> IncidentSeverity:
    """
    Derive the global incident severity from the longest CAUSAL chain, in hops (edges).

    Healthy   : no anomalies in the graph
    Degrading : anomalies observed, longest causal chain shorter than ``CRITICAL_MIN_HOPS``
    Critical  : longest causal chain spans ``CRITICAL_MIN_HOPS`` or more hops (A → B → C)
    """
    if not graph.nodes:
        return "Healthy"
    if _longest_causal_chain_hops(graph) < CRITICAL_MIN_HOPS:
        return "Degrading"
    return "Critical"


def _longest_causal_chain_hops(graph: IncidentGraph) -> int:
    """Number of edges on the longest CAUSAL path in the graph."""
    adj: dict[str, list[str]] = {}
    incoming_set: set[str] = set()
    for edge in graph.edges:
        if edge.edge_type is EdgeType.CAUSAL:
            adj.setdefault(str(edge.source), []).append(str(edge.target))
            incoming_set.add(str(edge.target))

    if not adj:
        return 0

    memo: dict[str, int] = {}

    def dfs(node_id: str) -> int:
        if node_id not in memo:
            memo[node_id] = max((1 + dfs(c) for c in adj.get(node_id, [])), default=0)
        return memo[node_id]

    roots = [str(n.node_id) for n in graph.nodes if str(n.node_id) not in incoming_set]
    if not roots:
        roots = list(adj.keys())  # Fallback if graph has a cycle (shouldn't happen)

    return max(dfs(r) for r in roots)

"""

Application use case: correlate a raw ``CoreEvent`` against the observation
window and update the ``IncidentGraph`` with any causal relationships found.

"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Literal

from deployd.application.ports.event_window_port import EventWindowPort
from deployd.domain.causal.causal_rule import DEFAULT_RULES, CorrelationRuleFn, RuleMatch
from deployd.domain.entities.core_event import CoreEvent
from deployd.domain.graph.edge import GraphEdge
from deployd.domain.graph.edge_type import EdgeType
from deployd.domain.graph.graph import DuplicateEdgeError, DuplicateNodeError, IncidentGraph
from deployd.domain.graph.node import GraphNode

log = logging.getLogger(__name__)

IncidentSeverity = Literal["Healthy", "Degrading", "Critical"]


class CorrelateEventsUseCase:
    """
    Streaming event correlation use case.

    Parameters
    ----------
    graph
        The ``IncidentGraph`` to write causal nodes and edges into.
    event_window
        An ``EventWindowPort`` implementation that maintains a time-bounded
        window of recent events.  Injected from the infrastructure layer.
    rules
        Ordered sequence of pure correlation rule functions from the domain
        layer.  Defaults to ``DEFAULT_RULES``.  Override in tests or for
        feature-flag-based rule toggling.
    """

    def __init__(
        self,
        graph: IncidentGraph,
        event_window: EventWindowPort,
        rules: Sequence[CorrelationRuleFn] | None = None,
    ) -> None:
        self._graph = graph
        self._window = event_window
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
            matches = rule_fn(event, snapshot)
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
    Derive the global incident severity from the causal chain topology.

    Thresholds
    ----------
    Healthy   : no nodes in the graph (no anomalies observed)
    Degrading : nodes exist but the longest causal chain is <= 2 hops
    Critical  : the longest causal chain spans 3 or more hops

    Parameters
    ----------
    graph
        The accumulated ``IncidentGraph`` after all events have been ingested.

    Returns
    -------
    Literal["Healthy", "Degrading", "Critical"]
    """
    if not graph.nodes:
        return "Healthy"

    depth = _max_causal_chain_depth(graph)
    if depth == 0:
        return "Healthy"
    if depth <= 2:
        return "Degrading"
    return "Critical"


def _max_causal_chain_depth(graph: IncidentGraph) -> int:
    """Return the number of hops in the longest CAUSAL path in the graph."""
    adj: dict[str, list[str]] = {}
    incoming_set: set[str] = set()
    for edge in graph.edges:
        if edge.edge_type is EdgeType.CAUSAL:
            adj.setdefault(str(edge.source), []).append(str(edge.target))
            incoming_set.add(str(edge.target))

    if not adj:
        return 0  # Nodes exist (anomalies) but no causal links yet

    memo: dict[str, int] = {}

    def dfs(node_id: str) -> int:
        if node_id in memo:
            return memo[node_id]
        children = adj.get(node_id, [])
        depth = 1 + (max((dfs(c) for c in children), default=0))
        memo[node_id] = depth
        return depth

    roots = [str(n.node_id) for n in graph.nodes if str(n.node_id) not in incoming_set]
    if not roots:
        roots = list(adj.keys())  # Fallback if graph has a cycle (shouldn't happen)

    return max(dfs(r) for r in roots)

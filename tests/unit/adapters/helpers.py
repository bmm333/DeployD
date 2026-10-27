"""Shared fixtures and helpers for repository adapter tests.

Both JSON and SQLite adapters must satisfy the same InvestigationRepository
contract.  This module provides builder helpers used by both test modules.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from deployd.domain.entities.core_event import CoreEvent, CoreEventType, Severity
from deployd.domain.entities.investigation import Investigation, TriggerType
from deployd.domain.graph.edge import GraphEdge
from deployd.domain.graph.edge_type import EdgeType
from deployd.domain.graph.graph import IncidentGraph
from deployd.domain.graph.node import GraphNode

NOW = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)


def make_investigation(**overrides: Any) -> Investigation:
    defaults: dict[str, Any] = {
        "trigger_type": TriggerType.ALERT,
        "started_at": NOW,
        "request": {"alert_id": "alert-99"},
    }
    defaults.update(overrides)
    return Investigation(**defaults)


def make_event(description: str = "test event") -> CoreEvent:
    return CoreEvent(
        event_type=CoreEventType.PROCESS_CRASH,
        timestamp=NOW,
        severity=Severity.CRITICAL,
        description=description,
    )


def make_graph_with_two_nodes() -> IncidentGraph:
    graph = IncidentGraph()
    make_event("deploy started")
    make_event("crash after deploy")
    # Need distinct event_ids → model_copy with new id
    n1 = GraphNode(
        event=CoreEvent(
            event_type=CoreEventType.DEPLOY_STARTED,
            timestamp=NOW,
            severity=Severity.INFO,
            description="deploy started",
        )
    )
    n2 = GraphNode(
        event=CoreEvent(
            event_type=CoreEventType.PROCESS_CRASH,
            timestamp=NOW,
            severity=Severity.CRITICAL,
            description="crash after deploy",
        )
    )
    graph.add_node(n1)
    graph.add_node(n2)
    graph.add_edge(
        GraphEdge(
            source=n1.node_id,
            target=n2.node_id,
            edge_type=EdgeType.CAUSAL,
            confidence=0.95,
            rule_id="rule-deploy-crash",
        )
    )
    return graph

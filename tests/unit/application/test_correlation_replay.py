"""Replaying a recorded scenario correlates the same regardless of when it is replayed."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from deployd.adapters.incoming.http_event_adapter import HttpEventAdapter, RawTelemetryEvent
from deployd.adapters.outgoing.registry.json_topology import load_topology
from deployd.application.use_cases.correlate_events import (
    CorrelateEventsUseCase,
    compute_incident_severity,
)
from deployd.domain.causal.config import CorrelationConfig
from deployd.domain.graph.graph import IncidentGraph
from deployd.infrastructure.streaming.sliding_window import SlidingWindow

DATA = Path("data")
SCENARIOS = sorted((DATA / "scenarios").glob("live_*.json"))


def _replay(
    events: list[dict[str, Any]], shift: timedelta
) -> tuple[list[tuple[str, ...]], str, int]:
    """Correlated edges (as descriptions), incident severity and node count."""
    config = CorrelationConfig(**json.loads((DATA / "correlation_config.json").read_text()))
    graph = IncidentGraph()
    use_case = CorrelateEventsUseCase(
        graph=graph,
        event_window=SlidingWindow(),
        config=config,
        topology=load_topology(DATA / "components.json"),
    )
    adapter = HttpEventAdapter()
    for raw in events:
        ts = datetime.fromisoformat(raw["timestamp"].replace("Z", "+00:00")) + shift
        use_case.ingest(
            adapter.translate(RawTelemetryEvent(**{**raw, "timestamp": ts.isoformat()}))
        )

    edges = []
    for edge in graph.edges:
        src, dst = graph.get_node(edge.source).event, graph.get_node(edge.target).event
        edges.append((src.description, dst.description, edge.rule_id or ""))
    return sorted(edges), compute_incident_severity(graph), len(graph.nodes)


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda p: p.stem)
def test_replay_is_independent_of_wall_clock(scenario: Path) -> None:
    events = json.loads(scenario.read_text())

    original = _replay(events, timedelta(0))
    a_year_later = _replay(events, timedelta(days=365))

    assert original == a_year_later


EXPECTED_SHAPE = {  # severity, nodes, edges
    "live_oom_auth_service": ("Critical", 3, 2),
    "live_search_es_cascade": ("Critical", 3, 2),
    "live_payment_db_timeout": ("Critical", 3, 2),
    "live_novel_analytics_db": ("Critical", 3, 2),
    "live_degrading_no_trigger": ("Degrading", 2, 1),
}


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda p: p.stem)
def test_demo_scenarios_keep_their_shape(scenario: Path) -> None:
    edges, severity, nodes = _replay(json.loads(scenario.read_text()), timedelta(0))

    assert (severity, nodes, len(edges)) == EXPECTED_SHAPE[scenario.stem]


def test_recorded_oom_scenario_still_correlates() -> None:
    events = json.loads((DATA / "scenarios" / "live_oom_auth_service.json").read_text())

    edges, severity, _ = _replay(events, timedelta(0))

    # config reload → memory 97% → gateway timeout; the auth-service health check is a
    # sibling symptom, not caused by the gateway (auth-service does not call it).
    assert len(edges) == 2
    assert severity == "Critical"

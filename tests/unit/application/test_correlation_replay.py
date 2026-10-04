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


def _replay(events: list[dict[str, Any]], shift: timedelta) -> tuple[list[tuple[str, ...]], str]:
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
    return sorted(edges), compute_incident_severity(graph)


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda p: p.stem)
def test_replay_is_independent_of_wall_clock(scenario: Path) -> None:
    events = json.loads(scenario.read_text())

    original = _replay(events, timedelta(0))
    a_year_later = _replay(events, timedelta(days=365))

    assert original == a_year_later


EXPECTED_SEVERITY = {
    "live_oom_auth_service": "Critical",
    "live_search_es_cascade": "Critical",
    "live_payment_db_timeout": "Critical",
    "live_novel_analytics_db": "Critical",
    "live_degrading_no_trigger": "Degrading",
}


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda p: p.stem)
def test_demo_scenarios_keep_their_severity(scenario: Path) -> None:
    _, severity = _replay(json.loads(scenario.read_text()), timedelta(0))

    assert severity == EXPECTED_SEVERITY[scenario.stem]


def test_recorded_oom_scenario_still_correlates() -> None:
    events = json.loads((DATA / "scenarios" / "live_oom_auth_service.json").read_text())

    edges, severity = _replay(events, timedelta(0))

    # config reload → memory 97% → gateway timeout; the auth-service health check is a
    # sibling symptom, not caused by the gateway (auth-service does not call it).
    assert len(edges) == 2
    assert severity == "Critical"

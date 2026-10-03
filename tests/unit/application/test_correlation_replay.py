"""Replaying a recorded scenario correlates the same regardless of when it is replayed."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from deployd.adapters.incoming.http_event_adapter import HttpEventAdapter, RawTelemetryEvent
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
    use_case = CorrelateEventsUseCase(graph=graph, event_window=SlidingWindow(), config=config)
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


def test_recorded_oom_scenario_still_correlates() -> None:
    events = json.loads((DATA / "scenarios" / "live_oom_auth_service.json").read_text())

    edges, severity = _replay(events, timedelta(0))

    assert len(edges) == 3
    assert severity == "Critical"

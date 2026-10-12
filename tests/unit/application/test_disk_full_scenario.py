"""DID-45: one instance of a peer group fills its disk. The peers are healthy and the release
reached all of them, so peer divergence must stay silent; nothing else explains it either."""

from __future__ import annotations

import json
from pathlib import Path

from deployd.adapters.incoming.http_event_adapter import HttpEventAdapter, RawTelemetryEvent
from deployd.adapters.outgoing.registry.json_topology import load_topology
from deployd.application.use_cases.correlate_events import CorrelateEventsUseCase
from deployd.domain.causal.config import CorrelationConfig
from deployd.domain.entities.core_event import CoreEventType
from deployd.domain.graph.graph import IncidentGraph
from deployd.infrastructure.streaming.sliding_window import SlidingWindow

DATA = Path("data")


def _graph() -> IncidentGraph:
    config = CorrelationConfig(**json.loads((DATA / "correlation_config.json").read_text()))
    graph = IncidentGraph()
    use_case = CorrelateEventsUseCase(
        graph=graph,
        event_window=SlidingWindow(change_retention_seconds=config.change_lookback_seconds),
        config=config,
        topology=load_topology(DATA / "components.json"),
    )
    adapter = HttpEventAdapter()
    raw = json.loads((DATA / "scenarios" / "live_disk_full_single_instance.json").read_text())
    for event in raw:
        use_case.ingest(adapter.translate(RawTelemetryEvent(**event)))
    return graph


def test_peer_divergence_stays_silent_when_the_release_reached_every_instance() -> None:
    graph = _graph()

    assert not [
        n for n in graph.nodes if n.event.event_type is CoreEventType.PEER_DIVERGENCE_DETECTED
    ]


def test_only_the_failing_instance_is_in_the_graph() -> None:
    graph = _graph()

    assert {n.event.related_component for n in graph.nodes} >= {"api-04"}
    assert not {n.event.related_component for n in graph.nodes} & {"api-01", "api-02", "api-03"}

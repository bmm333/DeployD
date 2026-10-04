"""Unrelated services must never end up in the same causal chain."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from deployd.adapters.incoming.http_event_adapter import HttpEventAdapter, RawTelemetryEvent
from deployd.application.use_cases.correlate_events import (
    CorrelateEventsUseCase,
    compute_incident_severity,
)
from deployd.domain.causal.config import CorrelationConfig
from deployd.domain.graph.edge_type import EdgeType
from deployd.domain.graph.graph import IncidentGraph
from deployd.infrastructure.streaming.sliding_window import SlidingWindow

T0 = datetime(2026, 9, 30, 10, 0, tzinfo=timezone.utc)


def test_unrelated_services_produce_no_causal_edges() -> None:
    graph = IncidentGraph()
    use_case = CorrelateEventsUseCase(
        graph=graph, event_window=SlidingWindow(), config=CorrelationConfig()
    )
    adapter = HttpEventAdapter()
    unrelated = [
        ("billing-batch", "CPU_SAMPLE", {"cpu_percent": 96}),
        ("shipping-service", "REQUEST_TIMEOUT", {"dependency": "carrier-api"}),
        ("search-ui", "HEALTHCHECK_FAIL", {}),
    ]
    for i, (source, event_type, metadata) in enumerate(unrelated):
        raw = RawTelemetryEvent(
            timestamp=(T0 + timedelta(seconds=10 * i)).isoformat(),
            source=source,
            event_type=event_type,
            metadata=metadata,
        )
        use_case.ingest(adapter.translate(raw))

    assert [e for e in graph.edges if e.edge_type is EdgeType.CAUSAL] == []
    assert compute_incident_severity(graph) != "Critical"

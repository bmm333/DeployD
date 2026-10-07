import json
from pathlib import Path

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

SCENARIOS_DIR = Path("data/scenarios")
COMPONENTS_FILE = Path("data/components.json")

EXPECTATIONS = {
    "live_payment_db_timeout.json": {"nodes": 3, "edges": 2, "severity": "Critical"},
    "live_degrading_no_trigger.json": {"nodes": 2, "edges": 1, "severity": "Degrading"},
    "live_novel_analytics_db.json": {"nodes": 3, "edges": 2, "severity": "Critical"},
    "live_oom_auth_service.json": {"nodes": 3, "edges": 2, "severity": "Critical"},
    "live_search_es_cascade.json": {"nodes": 3, "edges": 2, "severity": "Critical"},
}


@pytest.mark.parametrize("filename", EXPECTATIONS.keys())
def test_golden_scenario(filename: str):
    filepath = SCENARIOS_DIR / filename
    if not filepath.exists():
        pytest.skip(f"Scenario {filename} not found")

    raw_events = json.loads(filepath.read_text())

    # Modifica qui: valida con RawTelemetryEvent e traduci con translate()
    adapter = HttpEventAdapter()
    events = []
    for raw in raw_events:
        raw_telemetry = RawTelemetryEvent(**raw)
        events.append(adapter.translate(raw_telemetry))

    topology = load_topology(COMPONENTS_FILE)
    config = CorrelationConfig(
        database_components=["postgres", "elasticsearch"],
        metric_field_aliases={
            "latency_ms": ["latency_ms"],
            "memory_percent": ["memory_percent", "cpu_percent"],
            "status_code": ["status_code"],
        },
    )
    graph = IncidentGraph()
    window = SlidingWindow()
    use_case = CorrelateEventsUseCase(
        graph=graph, event_window=window, config=config, topology=topology
    )

    for event in events:
        use_case.ingest(event)

    expected = EXPECTATIONS[filename]
    assert (
        len(graph.nodes) == expected["nodes"]
    ), f"Expected {expected['nodes']} nodes, got {len(graph.nodes)}"
    assert (
        len(graph.edges) == expected["edges"]
    ), f"Expected {expected['edges']} edges, got {len(graph.edges)}"
    assert compute_incident_severity(graph) == expected["severity"]

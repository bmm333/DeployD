import json
import uuid
from pathlib import Path
from datetime import datetime
import pytest

from deployd.application.use_cases.correlate_events import CorrelateEventsUseCase, compute_incident_severity
from deployd.domain.entities.core_event import CoreEvent, CoreEventType, Severity
from deployd.infrastructure.streaming.sliding_window import SlidingWindow
from deployd.domain.causal.config import CorrelationConfig
from deployd.domain.graph.graph import IncidentGraph
from deployd.adapters.outgoing.registry.json_topology import load_topology

SCENARIOS_DIR = Path("data/scenarios")
COMPONENTS_FILE = Path("data/components.json")

EXPECTATIONS = {
    "live_payment_db_timeout.json": {"nodes": 3, "edges": 2, "severity": "Critical"},
    "live_degrading_no_trigger.json": {"nodes": 2, "edges": 1, "severity": "Degrading"},
    "live_novel_analytics_db.json": {"nodes": 3, "edges": 2, "severity": "Critical"},
    "live_oom_auth_service.json": {"nodes": 3, "edges": 2, "severity": "Critical"},
    "live_search_es_cascade.json": {"nodes": 3, "edges": 2, "severity": "Critical"},
}

def parse_event(raw: dict) -> CoreEvent:
    raw_type = raw["event_type"]
    mapping = {
        "LATENCY": CoreEventType.STATE_CHANGE,
        "REQUEST_TIMEOUT": CoreEventType.DEPENDENCY_FAILURE,
        "HEALTHCHECK_FAIL": CoreEventType.HEALTH_CHECK_FAIL,
        "CONFIG_RELOAD": CoreEventType.STATE_CHANGE,
        "MEMORY_SAMPLE": CoreEventType.RESOURCE_EXHAUSTION,
        "CPU_SAMPLE": CoreEventType.RESOURCE_EXHAUSTION,
    }
    
    event_type = mapping.get(raw_type, CoreEventType.STATE_CHANGE)
    
    meta = raw.get("metadata", {})
    meta["_raw_event_type"] = raw_type
        
    return CoreEvent(
        event_id=uuid.uuid4(),
        event_type=event_type,
        severity=Severity.ERROR,
        timestamp=datetime.fromisoformat(raw["timestamp"].replace("Z", "+00:00")),
        related_component=raw.get("source"),
        description=raw.get("description", ""),
        metadata=meta
    )

@pytest.mark.parametrize("filename", EXPECTATIONS.keys())
def test_golden_scenario(filename: str):
    filepath = SCENARIOS_DIR / filename
    if not filepath.exists():
        pytest.skip(f"Scenario {filename} not found")
        
    raw_events = json.loads(filepath.read_text())
    events = [parse_event(r) for r in raw_events]
    
    topology = load_topology(COMPONENTS_FILE)
    config = CorrelationConfig(
        database_components=["postgres", "elasticsearch"],
        metric_field_aliases={
            "latency_ms": ["latency_ms"],
            "memory_percent": ["memory_percent", "cpu_percent"],
            "status_code": ["status_code"],
        }
    )
    
    graph = IncidentGraph()
    window = SlidingWindow()
    use_case = CorrelateEventsUseCase(
        graph=graph,
        event_window=window,
        config=config,
        topology=topology
    )
    
    for event in events:
        use_case.ingest(event)
        
    expected = EXPECTATIONS[filename]
    assert len(graph.nodes) == expected["nodes"], f"Expected {expected['nodes']} nodes, got {len(graph.nodes)}"
    assert len(graph.edges) == expected["edges"], f"Expected {expected['edges']} edges, got {len(graph.edges)}"
    assert compute_incident_severity(graph) == expected["severity"]

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from deployd.adapters.incoming.simulator.scenario_loader import ScenarioDefinition, ScenarioLoader
from deployd.domain.entities.core_event import CoreEvent, CoreEventType, Severity
from deployd.domain.graph.edge_type import EdgeType


@pytest.fixture
def temp_scenario_file(tmp_path: Path) -> Path:
    """Creates a basic valid scenario JSON file."""
    data = {
        "scenario_id": "test-scenario",
        "description": "Test description",
        "component": "auth-service",
        "expected_tier": "FULL",
        "events": [
            {
                "event_type": "DEPLOY_STARTED",
                "severity": "INFO",
                "timestamp_offset_seconds": 0,
                "description": "Deploy started",
            },
            {
                "event_type": "PROCESS_CRASH",
                "severity": "CRITICAL",
                "timestamp_offset_seconds": 10,
                "description": "Process crashed",
                "causal_parent_index": 0,
            },
            {
                "event_type": "DEPLOY_COMPLETED",
                "severity": "WARNING",
                "timestamp_offset_seconds": 15,
                "description": "Process restarted",
                # No causal parent, should generate TEMPORAL edge
            },
        ],
    }
    file_path = tmp_path / "scenario.json"
    file_path.write_text(json.dumps(data))
    return file_path


def test_scenario_loader_valid(temp_scenario_file: Path) -> None:
    loader = ScenarioLoader()
    definition = loader.load(temp_scenario_file)

    assert definition.scenario_id == "test-scenario"
    assert len(definition.events) == 3
    assert definition.events[0].event_type == CoreEventType.DEPLOY_STARTED


def test_build_graph_edges(temp_scenario_file: Path) -> None:
    loader = ScenarioLoader()
    definition, raw_meta = loader.load_with_meta(temp_scenario_file)

    result = loader.build_graph(definition, raw_meta)

    assert len(result.node_ids) == 3

    # 2 edges should be created:
    # 1. 0 -> 1 (CAUSAL, from causal_parent_index)
    # 2. 1 -> 2 (TEMPORAL, fallback because causal_parent_index is missing)
    edges = result.graph.edges
    assert len(edges) == 2

    # Verify CAUSAL edge
    causal_edge = next(e for e in edges if e.edge_type == EdgeType.CAUSAL)
    assert causal_edge.source == result.node_ids[0]
    assert causal_edge.target == result.node_ids[1]
    assert causal_edge.confidence == 1.0
    assert causal_edge.rule_id == "scenario:causal_link"

    # Verify TEMPORAL edge
    temporal_edge = next(e for e in edges if e.edge_type == EdgeType.TEMPORAL)
    assert temporal_edge.source == result.node_ids[1]
    assert temporal_edge.target == result.node_ids[2]
    assert temporal_edge.confidence == 0.5
    assert temporal_edge.rule_id == "scenario:temporal_sequence"


def test_causal_parent_index_fail_fast(tmp_path: Path) -> None:
    data = {
        "scenario_id": "invalid-scenario",
        "description": "Test",
        "component": "auth",
        "expected_tier": "FULL",
        "events": [
            {
                "event_type": "PROCESS_CRASH",
                "severity": "CRITICAL",
                "causal_parent_index": 0,  # Invalid: >= current index (0)
                "description": "Crash",
            }
        ],
    }
    file_path = tmp_path / "invalid.json"
    file_path.write_text(json.dumps(data))

    loader = ScenarioLoader()
    with pytest.raises(ValueError, match="must come before child"):
        loader.load(file_path)


def test_resolve_query_explicit() -> None:
    loader = ScenarioLoader()
    definition = ScenarioDefinition(
        scenario_id="1",
        description="Fallback desc",
        component="test-comp",
        expected_tier="FULL",
        retrieval_query="explicit query",
        events=[],
    )
    assert loader.resolve_query(definition) == "explicit query"


def test_resolve_query_description_fallback() -> None:
    loader = ScenarioLoader()
    definition = ScenarioDefinition(
        scenario_id="1",
        description="Fallback desc",
        component="test-comp",
        expected_tier="FULL",
        retrieval_query=None,
        events=[],
    )
    assert loader.resolve_query(definition) == "Fallback desc"


def test_resolve_query_event_fallback() -> None:
    loader = ScenarioLoader()
    definition = ScenarioDefinition(
        scenario_id="1",
        description="",
        component="test-comp",
        expected_tier="FULL",
        retrieval_query=None,
        events=[
            CoreEvent(
                event_type=CoreEventType.PROCESS_CRASH,
                severity=Severity.CRITICAL,
                timestamp=datetime.now(timezone.utc),
                description="Crash",
            )
        ],
    )
    assert loader.resolve_query(definition) == "test-comp: PROCESS_CRASH"

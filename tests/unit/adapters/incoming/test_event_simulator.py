import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest
from deployd.adapters.incoming.simulator.event_simulator import EventSimulator
from deployd.application.dtos.diagnosis import DiagnosisTier


@pytest.fixture
def mock_data_dir(tmp_path: Path) -> Path:
    """Sets up a mock data directory with required files."""
    data_dir = tmp_path / "data"

    # 1. Scenarios
    scenarios_dir = data_dir / "scenarios"
    scenarios_dir.mkdir(parents=True)

    oom_scenario = {
        "scenario_id": "oom-kill",
        "description": "OOM",
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
                "description": "Crash",
                "causal_parent_index": 0,
            },
        ],
    }
    (scenarios_dir / "oom_kill_auth_service.json").write_text(json.dumps(oom_scenario))

    network_scenario = {
        "scenario_id": "network-partition",
        "description": "Network partition",
        "component": "payment-service",
        "expected_tier": "CHAIN_ONLY",
        "events": [
            {
                "event_type": "CONNECTIVITY_LOSS",
                "severity": "WARNING",
                "timestamp_offset_seconds": 0,
                "description": "Packet loss",
            },
            {
                "event_type": "DEPENDENCY_FAILURE",
                "severity": "CRITICAL",
                "timestamp_offset_seconds": 10,
                "description": "Dependency timeout",
                "causal_parent_index": 0,
            },
        ],
    }
    (scenarios_dir / "novel_network_partition.json").write_text(json.dumps(network_scenario))

    # 2. Runbooks & Chroma
    runbooks_dir = data_dir / "runbooks"
    runbooks_dir.mkdir(parents=True)
    (data_dir / "chroma").mkdir(parents=True, exist_ok=True)

    # We add a matching runbook for auth-service OOM
    rb_oom = {
        "runbook_id": "RB-OOM",
        "incident_id": "INC-1",
        "summary": "auth-service OOMKill",
        "affected_components": ["auth-service"],
        "root_cause": "Memory leak",
        "fix": "Revert deploy",
        "fix_commands": [],
        "causal_chain": [],
        "tags": ["memory"],
    }
    (runbooks_dir / "rb_oom.json").write_text(json.dumps(rb_oom))

    # 3. Components
    components_file = data_dir / "components.json"
    components_file.write_text(json.dumps({"auth-service": {}, "payment-service": {}}))

    # 4. Constraints
    constraints_file = data_dir / "compatibility_constraints.json"
    constraints_file.write_text(json.dumps({}))

    return data_dir


def test_event_simulator_offline_mode_oom(mock_data_dir: Path) -> None:
    """Test the OOM scenario using the StubAgent (offline mode)."""
    # Ensure GROQ_API_KEY is unset so it falls back to StubAgent
    with patch.dict(os.environ, {}, clear=True):
        simulator = EventSimulator(data_dir=mock_data_dir)

        scenario_path = mock_data_dir / "scenarios" / "oom_kill_auth_service.json"

        # Patch ChromaClient and BM25 to avoid actual vector store logic
        # We patch RetrieveCandidates so we can control what the retriever finds
        with (
            patch("deployd.adapters.incoming.simulator.event_simulator.ChromaRunbookClient"),
            patch("deployd.adapters.incoming.simulator.event_simulator.BM25RunbookIndex"),
            patch(
                "deployd.adapters.incoming.simulator.event_simulator.RetrieveCandidates"
            ) as mock_retriever_cls,
        ):
            mock_retriever = mock_retriever_cls.return_value
            from deployd.application.dtos.retrieval import RetrievalCandidate, RetrievalResult

            # For OOM, return a strong match
            mock_retriever.execute.return_value = RetrievalResult(
                candidates=[RetrievalCandidate(runbook_id="RB-OOM", score=0.9)],
                confidence_threshold=0.5,
            )

            result = simulator.run(scenario_path)

        assert result.scenario_id == "oom-kill"
        assert result.agent_mode.startswith("offline/stub")
        assert result.expected_tier == "FULL"
        # StubAgent + strong match -> FULL
        assert result.diagnosis.tier == DiagnosisTier.FULL


def test_event_simulator_offline_mode_network_partition(mock_data_dir: Path) -> None:
    """Test the network partition scenario using the StubAgent (offline mode)."""
    with patch.dict(os.environ, {}, clear=True):
        simulator = EventSimulator(data_dir=mock_data_dir)

        scenario_path = mock_data_dir / "scenarios" / "novel_network_partition.json"

        with (
            patch("deployd.adapters.incoming.simulator.event_simulator.ChromaRunbookClient"),
            patch("deployd.adapters.incoming.simulator.event_simulator.BM25RunbookIndex"),
            patch(
                "deployd.adapters.incoming.simulator.event_simulator.RetrieveCandidates"
            ) as mock_retriever_cls,
        ):
            mock_retriever = mock_retriever_cls.return_value
            from deployd.application.dtos.retrieval import RetrievalResult

            # For network partition, return NO strong match
            mock_retriever.execute.return_value = RetrievalResult(
                candidates=[],
                confidence_threshold=0.5,
            )

            result = simulator.run(scenario_path)

        assert result.scenario_id == "network-partition"
        assert result.agent_mode.startswith("offline/stub")
        assert result.expected_tier == "CHAIN_ONLY"
        # StubAgent + no strong match -> CHAIN_ONLY
        assert result.diagnosis.tier == DiagnosisTier.CHAIN_ONLY

import threading
from unittest.mock import Mock

from deployd.entrypoints import api
from deployd.entrypoints.api import app
from deployd.infrastructure.persistence.sqlite_incident_repository import SQLiteIncidentRepository
from fastapi.testclient import TestClient

client = TestClient(app)


def test_reset_during_investigation(monkeypatch, tmp_path):
    """
    Test: reset during a (stubbed, slow) investigation leaves the new incident clean.
    """
    # 1. Use a temporary database
    temp_db = tmp_path / "test.db"
    mock_repo = SQLiteIncidentRepository(db_path=temp_db)
    monkeypatch.setattr(api, "_incident_repo", mock_repo)
    monkeypatch.setattr(api._lifecycle, "_repo", mock_repo)
    monkeypatch.setattr(api._list_incidents, "_repo", mock_repo)
    monkeypatch.setattr(api._get_incident, "_repo", mock_repo)

    # 2. Stub retrieval and agent
    mock_retrieval = Mock()
    mock_retriever = Mock()
    mock_retriever.retrieve_scored.return_value = (
        Mock(candidates=[], confidence_threshold=0.0),
        {},
    )
    mock_retrieval.retriever = mock_retriever
    monkeypatch.setattr(api, "_get_retrieval", lambda: mock_retrieval)
    monkeypatch.setattr(api, "_get_agent", lambda: None)

    # 3. We want to mock InvestigationOrchestrator.run to wait on an event
    from deployd.application.orchestrators.investigation_orchestrator import (
        InvestigationOrchestrator,
    )

    original_run = InvestigationOrchestrator.run
    investigation_started = threading.Event()
    investigation_proceed = threading.Event()

    def slow_run(self, request):
        investigation_started.set()
        investigation_proceed.wait()
        return original_run(self, request)

    monkeypatch.setattr(InvestigationOrchestrator, "run", slow_run)

    # Reset state initially
    client.post("/api/v1/reset")

    # 4. Trigger an investigation (needs a Critical event, e.g. 3 consecutive anomalies)
    events = [
        {
            "timestamp": "2024-01-01T12:00:00Z",
            "source": "payment-service",
            "event_type": "CPU_SAMPLE",
            "metadata": {"cpu_percent": 99},
            "description": "High CPU usage 1",
        },
        {
            "timestamp": "2024-01-01T12:01:00Z",
            "source": "payment-service",
            "event_type": "CPU_SAMPLE",
            "metadata": {"cpu_percent": 99},
            "description": "High CPU usage 2",
        },
        {
            "timestamp": "2024-01-01T12:02:00Z",
            "source": "payment-service",
            "event_type": "CPU_SAMPLE",
            "metadata": {"cpu_percent": 99},
            "description": "High CPU usage 3",
        },
    ]

    for ev in events[:-1]:
        client.post("/api/v1/events", json=ev)

    # TestClient runs background tasks synchronously.
    # We must send the final event in a separate thread so it blocks there and we can call reset in the main thread.
    def send_final_event():
        client.post("/api/v1/events", json=events[-1])

    t = threading.Thread(target=send_final_event)
    t.start()

    # Wait for the investigation to start and block
    investigation_started.wait(timeout=5.0)

    # 5. Call reset while investigation is running
    client.post("/api/v1/reset")

    # Let the investigation finish
    investigation_proceed.set()
    t.join(timeout=5.0)

    # 6. Check the state. The new incident should be clean (no decision trace, no session).
    state_response = client.get("/api/v1/state")
    assert state_response.status_code == 200
    state = state_response.json()

    assert state["decision_trace"] is None
    assert state["session"] is None
    assert len(state["chat_history"]) == 0
    assert state["investigating"] is False

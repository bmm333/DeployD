import threading
import time
import uuid

import pytest
from fastapi.testclient import TestClient

from deployd.entrypoints.api import app, _incident_repo
from deployd.adapters.incoming.http_event_adapter import RawTelemetryEvent

client = TestClient(app)

def test_reset_during_investigation(monkeypatch):
    """
    Test: reset during a (stubbed, slow) investigation leaves the new incident clean.
    """
    # We want to mock InvestigationOrchestrator.run to be slow
    from deployd.application.orchestrators.investigation_orchestrator import InvestigationOrchestrator
    
    original_run = InvestigationOrchestrator.run
    
    def slow_run(self, request):
        time.sleep(1.0)
        return original_run(self, request)

    monkeypatch.setattr(InvestigationOrchestrator, "run", slow_run)
    
    # 1. Reset state
    client.post("/api/v1/reset")
    
    # 2. Trigger an investigation (needs a Critical event)
    # Actually, we can just send enough events to trigger critical and let the background task run
    event1 = {
        "timestamp": "2024-01-01T12:00:00Z",
        "source": "payment-service",
        "event_type": "CPU_SAMPLE",
        "metadata": {"cpu_percent": 99},
        "description": "High CPU usage"
    }
    
    response = client.post("/api/v1/events", json=event1)
    assert response.status_code == 200
    
    # Wait briefly to let the background task start running
    time.sleep(0.3)
    
    # 3. Call reset while investigation is running
    client.post("/api/v1/reset")
    
    # Wait for the slow investigation to finish
    time.sleep(1.5)
    
    # 4. Check the state. The new incident should be clean (no decision trace, no session).
    state_response = client.get("/api/v1/state")
    assert state_response.status_code == 200
    state = state_response.json()
    
    assert state["decision_trace"] is None
    assert state["session"] is None
    assert len(state["chat_history"]) == 0
    assert state["investigating"] is False

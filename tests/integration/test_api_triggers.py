"""When the live API starts an investigation: a 2+ hop chain, or a crash in the incident."""

import json
from pathlib import Path
from unittest.mock import Mock

import pytest
from deployd.application.dtos.retrieval import RetrievalResult
from deployd.entrypoints import api
from deployd.entrypoints.api import app
from deployd.infrastructure.persistence.sqlite_incident_repository import SQLiteIncidentRepository
from fastapi.testclient import TestClient

client = TestClient(app)

PAYMENT_EVENTS = json.loads(Path("data/scenarios/live_payment_db_timeout.json").read_text())


def _event(second: int, source: str, event_type: str, **metadata: object) -> dict[str, object]:
    return {
        "timestamp": f"2026-09-30T09:5{second // 60}:{second % 60:02d}Z",
        "source": source,
        "event_type": event_type,
        "metadata": metadata,
        "description": f"{source} {event_type.lower()}",
    }


# auth-service runs out of memory (an isolated anomaly), then the pod is OOM-killed.
MEMORY_THEN_OOM = [
    _event(0, "auth-service", "MEMORY_SAMPLE", memory_percent=97),
    _event(20, "auth-service", "OOM_KILL"),
]


@pytest.fixture(autouse=True)
def _offline(monkeypatch, tmp_path):
    repo = SQLiteIncidentRepository(db_path=tmp_path / "triggers.db")
    monkeypatch.setattr(api, "_incident_repo", repo)
    monkeypatch.setattr(api._lifecycle, "_repo", repo)
    retrieval = Mock()
    retrieval.retriever.retrieve_scored.return_value = (RetrievalResult(), {})
    monkeypatch.setattr(api, "_get_retrieval", lambda: retrieval)
    monkeypatch.setattr(api, "_get_agent", lambda: None)
    client.post("/api/v1/reset")


def _send(events: list[dict[str, object]]) -> dict:
    for event in events:
        client.post("/api/v1/events", json=event)
    return client.get("/api/v1/state").json()


def test_a_crash_of_an_incident_component_starts_the_investigation():
    state = _send(MEMORY_THEN_OOM)

    assert state["decision_trace"] is not None
    announcement = next(m for m in state["chat_history"] if m["kind"] == "event")
    assert "auth-service is CRASHING, no causal chain yet" in announcement["content"]


def test_a_crash_outside_the_incident_does_not_use_up_its_investigation():
    _send([_event(0, "billing-worker", "OOM_KILL")])

    state = _send(PAYMENT_EVENTS)

    assert state["decision_trace"] is not None
    assert "reached CRITICAL" in state["chat_history"][0]["content"]


def test_reset_starts_a_fresh_health_tracker():
    _send(MEMORY_THEN_OOM)
    client.post("/api/v1/reset")

    # Same event times: without a fresh tracker its cooldown would swallow the crash.
    state = _send(MEMORY_THEN_OOM)

    assert state["decision_trace"] is not None

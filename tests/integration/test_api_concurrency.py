import json
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import Mock

from deployd.application.dtos.retrieval import RetrievalResult
from deployd.application.orchestrators.investigation_orchestrator import (
    InvestigationOrchestrator,
)
from deployd.entrypoints import api
from deployd.entrypoints.api import app
from deployd.infrastructure.persistence.sqlite_incident_repository import SQLiteIncidentRepository
from fastapi.testclient import TestClient

client = TestClient(app)

# Postgres latency -> payment timeout -> checkout health check: a 2-hop chain, so CRITICAL.
CRITICAL_EVENTS = json.loads(Path("data/scenarios/live_payment_db_timeout.json").read_text())


def test_reset_during_investigation(monkeypatch, tmp_path):
    """A reset while an investigation runs must neither crash it nor leak its results."""
    repo = SQLiteIncidentRepository(db_path=tmp_path / "test.db")
    monkeypatch.setattr(api, "_incident_repo", repo)
    monkeypatch.setattr(api._lifecycle, "_repo", repo)
    monkeypatch.setattr(api._list_incidents, "_repo", repo)
    monkeypatch.setattr(api._get_incident, "_repo", repo)

    retrieval = Mock()
    retrieval.retriever.retrieve_scored.return_value = (RetrievalResult(), {})
    monkeypatch.setattr(api, "_get_retrieval", lambda: retrieval)
    monkeypatch.setattr(api, "_get_agent", lambda: None)

    original_run = InvestigationOrchestrator.run
    investigation_started = threading.Event()
    investigation_proceed = threading.Event()

    def blocking_run(self, request):
        investigation_started.set()
        investigation_proceed.wait(timeout=5)
        return original_run(self, request)

    monkeypatch.setattr(InvestigationOrchestrator, "run", blocking_run)

    client.post("/api/v1/reset")
    for event in CRITICAL_EVENTS[:-1]:
        client.post("/api/v1/events", json=event)

    # TestClient runs background tasks before returning, so the event that triggers
    # the investigation is sent from a worker and the reset happens meanwhile;
    # result() re-raises anything the investigation raised.
    with ThreadPoolExecutor(max_workers=1) as pool:
        trigger = pool.submit(client.post, "/api/v1/events", json=CRITICAL_EVENTS[-1])
        assert investigation_started.wait(timeout=5), "the investigation never started"

        client.post("/api/v1/reset")
        investigation_proceed.set()
        trigger.result(timeout=5)

    state = client.get("/api/v1/state").json()
    assert state["decision_trace"] is None
    assert state["session"] is None
    assert state["chat_history"] == []
    assert state["investigating"] is False


def test_retrieval_stack_is_built_once_under_concurrency(monkeypatch):
    built = []

    class SlowChroma:
        def __init__(self, persist_directory):
            built.append(persist_directory)
            threading.Event().wait(0.05)  # widen the race window

        def index_runbook(self, *args):
            pass

    monkeypatch.setattr(api, "ChromaRunbookClient", SlowChroma)
    monkeypatch.setattr(api, "_retrieval", None)

    with ThreadPoolExecutor(max_workers=8) as pool:
        stacks = list(pool.map(lambda _: api._get_retrieval(), range(8)))

    assert len(built) == 1
    assert all(stack is stacks[0] for stack in stacks)

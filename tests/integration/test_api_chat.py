import json
from pathlib import Path
from types import SimpleNamespace

from deployd.application.dtos.diagnosis import AgentDiagnosis
from deployd.entrypoints import api
from deployd.entrypoints.api import app
from deployd.infrastructure.persistence.sqlite_incident_repository import SQLiteIncidentRepository
from fastapi.testclient import TestClient

client = TestClient(app)

FIRST_EVENT = json.loads(Path("data/scenarios/live_payment_db_timeout.json").read_text())[0]


def test_follow_up_answer_carries_confidence_and_evidence(monkeypatch, tmp_path):
    repo = SQLiteIncidentRepository(db_path=tmp_path / "chat.db")
    monkeypatch.setattr(api, "_incident_repo", repo)
    monkeypatch.setattr(api._lifecycle, "_repo", repo)
    client.post("/api/v1/reset")
    client.post("/api/v1/events", json=FIRST_EVENT)  # opens the incident

    answer = AgentDiagnosis(
        root_cause="Check the connection pool size first.",
        confidence="Low",
        reasoning="Follow-up turn 1.",
        recommendation="Confirm with the team.",
        evidence_references=["RB-PAYMENT-DB-TIMEOUT"],
    )
    agent = SimpleNamespace(follow_up=lambda session_id, prompt: answer, last_token_usage=42)
    monkeypatch.setattr(api, "_get_agent", lambda: agent)
    monkeypatch.setattr(api, "_decision_trace", {"tier": "FULL", "llm_error": None})
    monkeypatch.setattr(
        api, "_session", {"id": "s1", "component": "postgres-primary", "turn": 0, "max_turns": 5}
    )

    client.post("/api/v1/chat", json={"prompt": "what should I check first?"})

    last = client.get("/api/v1/state").json()["chat_history"][-1]
    assert last["kind"] == "followup"
    assert last["content"] == answer.root_cause
    assert last["confidence"] == "Low"
    assert last["evidence"] == "RB-PAYMENT-DB-TIMEOUT"

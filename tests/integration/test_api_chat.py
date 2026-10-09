import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from deployd.application.dtos.diagnosis import AgentDiagnosis
from deployd.application.dtos.retrieval import RetrievalCandidate, RetrievalResult
from deployd.application.orchestrators.investigation_orchestrator import UNVERIFIABLE_ANSWER
from deployd.entrypoints import api
from deployd.entrypoints.api import app
from deployd.infrastructure.persistence.sqlite_incident_repository import SQLiteIncidentRepository
from fastapi.testclient import TestClient

client = TestClient(app)

# Postgres latency -> payment timeout -> checkout health check: CRITICAL, so it is investigated.
PAYMENT_EVENTS = json.loads(Path("data/scenarios/live_payment_db_timeout.json").read_text())
FIRST_EVENT = PAYMENT_EVENTS[0]


def _use_temp_repo(monkeypatch, tmp_path):
    repo = SQLiteIncidentRepository(db_path=tmp_path / "chat.db")
    monkeypatch.setattr(api, "_incident_repo", repo)
    monkeypatch.setattr(api._lifecycle, "_repo", repo)


def test_answer_without_verifiable_citation_is_withheld(monkeypatch, tmp_path):
    _use_temp_repo(monkeypatch, tmp_path)
    retrieval = Mock()
    retrieval.retriever.retrieve_scored.return_value = (
        RetrievalResult(candidates=[RetrievalCandidate("RB-PAYMENT-DB-TIMEOUT", 0.9)]),
        {},
    )
    unsupported = AgentDiagnosis(
        root_cause="Restart everything.",
        confidence="High",
        reasoning="Nothing it cited survived validation.",
        recommendation="Restart everything.",
        evidence_references=[],
    )
    agent = SimpleNamespace(
        diagnose=lambda **_: unsupported,
        last_session_id="s1",
        last_token_usage=77,
        prompt_version="1.3.0",
    )
    monkeypatch.setattr(api, "_get_retrieval", lambda: retrieval)
    monkeypatch.setattr(api, "_get_agent", lambda: agent)

    client.post("/api/v1/reset")
    for event in PAYMENT_EVENTS:
        client.post("/api/v1/events", json=event)

    state = client.get("/api/v1/state").json()
    trace = state["decision_trace"]
    assert (trace["tier"], trace["llm_called"], trace["tokens_used"]) == ("FULL", True, 77)
    assert trace["answer_discarded"] == UNVERIFIABLE_ANSWER
    assert state["session"] is None
    shown = state["chat_history"][-1]
    assert shown["kind"] == "deterministic"
    assert shown["reason"] == UNVERIFIABLE_ANSWER
    assert "Restart everything" not in json.dumps(state["chat_history"])

    client.post("/api/v1/chat", json={"prompt": "so what do I do?"})

    refusal = client.get("/api/v1/state").json()["chat_history"][-1]
    assert refusal["kind"] == "gate"
    assert UNVERIFIABLE_ANSWER in refusal["content"]


def test_follow_up_answer_carries_confidence_and_evidence(monkeypatch, tmp_path):
    _use_temp_repo(monkeypatch, tmp_path)
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

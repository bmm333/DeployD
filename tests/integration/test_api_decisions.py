"""Human-in-the-loop decisions on the agent's answers (DID-38)."""

import json
from pathlib import Path
from unittest.mock import Mock

import pytest
from deployd.application.dtos.diagnosis import AgentDiagnosis
from deployd.application.dtos.retrieval import RetrievalCandidate, RetrievalResult
from deployd.entrypoints import api
from deployd.entrypoints.api import app
from deployd.infrastructure.persistence.sqlite_incident_repository import SQLiteIncidentRepository
from fastapi.testclient import TestClient

client = TestClient(app)

PAYMENT_EVENTS = json.loads(Path("data/scenarios/live_payment_db_timeout.json").read_text())
RUNBOOK = "RB-PAYMENT-DB-TIMEOUT"


class _Agent:
    """Stub Tier-3 agent: a fixed diagnosis, and a revision for every follow-up."""

    last_session_id = "s1"
    last_token_usage = 100
    last_removed_citations: list[str] = []
    prompt_version = "1.3.0"
    model_id = "openai/gpt-oss-120b"

    def __init__(self) -> None:
        self.follow_ups: list[str] = []

    def diagnose(self, **_: object) -> AgentDiagnosis:
        return AgentDiagnosis(
            root_cause="Connection pool exhaustion on postgres-primary.",
            confidence="High",
            reasoning="latency -> timeout -> health check",
            recommendation="Raise the pool size back to 50.",
            evidence_references=[RUNBOOK],
        )

    def follow_up(self, session_id: str, message: str) -> AgentDiagnosis:
        self.follow_ups.append(message)
        return AgentDiagnosis(
            root_cause="Revised: a slow migration holds the connections; kill it first.",
            confidence="Medium",
            reasoning="follow-up",
            recommendation="Review the answer.",
            evidence_references=[RUNBOOK],
        )


@pytest.fixture
def agent(monkeypatch, tmp_path) -> _Agent:
    repo = SQLiteIncidentRepository(db_path=tmp_path / "decisions.db")
    monkeypatch.setattr(api, "_incident_repo", repo)
    for use_case in (api._lifecycle, api._get_incident, api._list_incidents):
        monkeypatch.setattr(use_case, "_repo", repo)
    retrieval = Mock()
    retrieval.retriever.retrieve_scored.return_value = (
        RetrievalResult(candidates=[RetrievalCandidate(RUNBOOK, 0.9)]),
        {},
    )
    stub = _Agent()
    monkeypatch.setattr(api, "_get_retrieval", lambda: retrieval)
    monkeypatch.setattr(api, "_get_agent", lambda: stub)
    client.post("/api/v1/reset")
    for event in PAYMENT_EVENTS:
        client.post("/api/v1/events", json=event)
    return stub


def _decide(**body: object):
    incident_id = client.get("/api/v1/state").json()["incident_id"]
    return client.post(
        f"/api/v1/incidents/{incident_id}/decision", json={"engineer": "ben", **body}
    )


def _incident() -> dict:
    incident_id = client.get("/api/v1/state").json()["incident_id"]
    return client.get(f"/api/v1/incidents/{incident_id}").json()


def test_an_approval_is_recorded_with_the_answer_it_approves(agent):
    assert client.get("/api/v1/state").json()["review"]["status"] == "pending"

    assert _decide(decision="ACCEPT").status_code == 200

    state = client.get("/api/v1/state").json()
    assert state["review"]["status"] == "approved"
    assert state["chat_history"][-1]["kind"] == "decision"
    assert state["decision_trace"]["human_decision"]["decision"] == "ACCEPT"
    [decision] = _incident()["decisions"]
    assert decision["recommendation"] == "Raise the pool size back to 50."
    assert decision["evidence_references"] == [RUNBOOK]
    assert (decision["model"], decision["prompt_version"]) == ("openai/gpt-oss-120b", "1.3.0")
    assert agent.follow_ups == []
    assert _decide(decision="ACCEPT").status_code == 409  # nothing left to decide on


def test_a_rejection_goes_back_to_the_agent_and_its_revision_is_reviewed_again(agent):
    _decide(decision="REJECT", comment="The pool is already at 50.")

    assert "The pool is already at 50." in agent.follow_ups[0]
    state = client.get("/api/v1/state").json()
    revision = state["chat_history"][-1]
    assert (revision["kind"], revision["version"]) == ("revision", "2")
    assert state["review"] == {
        "version": 2,
        "root_cause": revision["content"],
        "recommendation": revision["content"],
        "evidence": [RUNBOOK],
        "status": "pending",
    }

    _decide(decision="AMEND", amended_recommendation="Kill the migration, then raise the pool.")

    decisions = _incident()["decisions"]
    assert [(d["decision"], d["diagnosis_version"]) for d in decisions] == [
        ("REJECT", 1),
        ("AMEND", 2),
    ]
    assert decisions[1]["amended_recommendation"] == "Kill the migration, then raise the pool."


@pytest.mark.parametrize(
    "body",
    [
        {"decision": "REJECT"},
        {"decision": "REJECT", "comment": "  "},
        {"decision": "AMEND"},
        {"decision": "ACCEPT", "engineer": " "},
    ],
)
def test_a_decision_without_what_it_needs_is_refused(agent, body):
    assert _decide(**body).status_code == 422
    assert client.get("/api/v1/state").json()["review"]["status"] == "pending"


def test_only_the_open_incident_takes_decisions(agent):
    response = client.post(
        "/api/v1/incidents/00000000-0000-0000-0000-000000000000/decision",
        json={"engineer": "ben", "decision": "ACCEPT"},
    )

    assert response.status_code == 409


def test_without_turns_left_a_rejection_is_recorded_but_not_revised(agent, monkeypatch):
    monkeypatch.setitem(api._session, "turn", api._session["max_turns"])

    _decide(decision="REJECT", comment="Wrong service.")

    state = client.get("/api/v1/state").json()
    assert agent.follow_ups == []
    assert state["review"]["status"] == "closed"
    assert "no turns left" in state["chat_history"][-1]["content"]


def test_decisions_stay_on_the_incident_after_it_is_closed(agent):
    incident_id = client.get("/api/v1/state").json()["incident_id"]
    _decide(decision="ACCEPT")

    client.post("/api/v1/reset")

    detail = client.get(f"/api/v1/incidents/{incident_id}").json()
    assert detail["status"] == "RESOLVED"
    assert [d["decision"] for d in detail["decisions"]] == ["ACCEPT"]
    listed = next(i for i in client.get("/api/v1/incidents").json() if i["id"] == incident_id)
    assert listed["last_decision"] == "ACCEPT"

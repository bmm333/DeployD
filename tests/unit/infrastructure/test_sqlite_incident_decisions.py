"""SQLiteIncidentRepository keeps the engineer decisions of an incident (DID-38)."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from deployd.domain.entities.human_decision import DecisionValue, HumanDecision
from deployd.domain.incident.incident import Incident
from deployd.infrastructure.persistence.sqlite_incident_repository import SQLiteIncidentRepository

T0 = datetime(2026, 10, 11, 10, 0, tzinfo=timezone.utc)


def _amend(incident: Incident) -> HumanDecision:
    return HumanDecision(
        incident_id=incident.id,
        decision=DecisionValue.AMEND,
        engineer_id="ben",
        timestamp=T0,
        root_cause="cache growth",
        recommendation="roll back",
        amended_recommendation="raise the heap to 2 GiB",
        evidence_references=["RB-AUTH-SERVICE-OOMKILL"],
        model="openai/gpt-oss-120b",
        prompt_version="1.3.0",
    )


def test_decisions_survive_a_round_trip(tmp_path) -> None:
    repo = SQLiteIncidentRepository(tmp_path / "incidents.db")
    incident = Incident(opened_at=T0)
    incident.record_decision(_amend(incident))

    repo.save(incident)

    assert repo.get(incident.id).decisions == incident.decisions


def test_a_database_from_before_decisions_is_migrated(tmp_path) -> None:
    path = tmp_path / "old.db"
    with sqlite3.connect(path) as conn:
        conn.execute(
            "CREATE TABLE incidents (id TEXT PRIMARY KEY, opened_at TEXT NOT NULL, "
            "resolved_at TEXT, status TEXT NOT NULL DEFAULT 'OPEN', "
            "peak_severity TEXT NOT NULL DEFAULT 'INFO', "
            "graph_snapshot TEXT NOT NULL DEFAULT '{}', chat_history TEXT NOT NULL DEFAULT '[]', "
            "root_cause_summary TEXT)"
        )
        conn.execute(
            "INSERT INTO incidents (id, opened_at) VALUES (?, ?)",
            ("8f9c7a52-1b6e-4b0a-9d4e-1f2a3b4c5d6e", T0.isoformat()),
        )

    repo = SQLiteIncidentRepository(path)
    incident = repo.get_current()
    assert incident is not None and incident.decisions == []

    incident.record_decision(_amend(incident))
    repo.save(incident)
    assert repo.get(incident.id).decisions[0].amended_recommendation == "raise the heap to 2 GiB"


def test_the_decision_trace_survives_a_round_trip(tmp_path) -> None:
    repo = SQLiteIncidentRepository(tmp_path / "incidents.db")
    incident = Incident(opened_at=T0)
    incident.decision_trace = {"tier": "FULL", "causal_chain_length": 2, "rules_fired": ["R2"]}

    repo.save(incident)

    assert repo.get(incident.id).decision_trace == incident.decision_trace
    assert repo.get(Incident(opened_at=T0).id) is None


def test_an_incident_without_a_trace_loads_with_none(tmp_path) -> None:
    repo = SQLiteIncidentRepository(tmp_path / "incidents.db")
    incident = Incident(opened_at=T0)
    repo.save(incident)

    assert repo.get(incident.id).decision_trace is None


def test_a_database_from_before_the_trace_column_is_migrated(tmp_path) -> None:
    path = tmp_path / "before-dids-38-39.db"
    with sqlite3.connect(path) as conn:
        conn.execute(
            "CREATE TABLE incidents (id TEXT PRIMARY KEY, opened_at TEXT NOT NULL, "
            "resolved_at TEXT, status TEXT NOT NULL DEFAULT 'OPEN', "
            "peak_severity TEXT NOT NULL DEFAULT 'INFO', "
            "graph_snapshot TEXT NOT NULL DEFAULT '{}', chat_history TEXT NOT NULL DEFAULT '[]', "
            "root_cause_summary TEXT, decisions TEXT NOT NULL DEFAULT '[]')"
        )
        conn.execute(
            "INSERT INTO incidents (id, opened_at) VALUES (?, ?)",
            ("8f9c7a52-1b6e-4b0a-9d4e-1f2a3b4c5d6e", T0.isoformat()),
        )

    repo = SQLiteIncidentRepository(path)
    incident = repo.get_current()
    assert incident is not None and incident.decision_trace is None

    incident.decision_trace = {"tier": "CHAIN_ONLY"}
    repo.save(incident)
    assert repo.get(incident.id).decision_trace == {"tier": "CHAIN_ONLY"}

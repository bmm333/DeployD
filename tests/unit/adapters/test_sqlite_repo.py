"""Tests for SqliteInvestigationRepository.

Same contract as JsonInvestigationRepository, exercised against an in-memory
SQLite database.  Shared helpers from tests.unit.adapters.helpers keep
the two test modules symmetric.
"""

from __future__ import annotations

import uuid

import pytest
from deployd.domain.entities.investigation import InvestigationStatus
from deployd.domain.graph.edge_type import EdgeType
from deployd.domain.repositories.investigation_repo import (
    InvestigationNotFoundError,
    InvestigationRepository,
)
from deployd.infrastructure.persistence.sqlite_investigation_repo import (
    SqliteInvestigationRepository,
)
from tests.unit.adapters.helpers import (
    make_event,
    make_graph_with_two_nodes,
    make_investigation,
)


@pytest.fixture()
def repo() -> SqliteInvestigationRepository:
    return SqliteInvestigationRepository(db_path=":memory:")


# ---------------------------------------------------------------------------
# Protocol conformance
# ---------------------------------------------------------------------------


class TestProtocolConformance:
    def test_adapter_satisfies_protocol(self) -> None:
        r = SqliteInvestigationRepository(db_path=":memory:")
        assert isinstance(r, InvestigationRepository)


# ---------------------------------------------------------------------------
# save + get
# ---------------------------------------------------------------------------


class TestSaveAndGet:
    def test_save_and_get_round_trip(self, repo) -> None:
        inv = make_investigation()
        repo.save(inv)
        restored = repo.get(inv.investigation_id)
        assert restored.investigation_id == inv.investigation_id
        assert restored.trigger_type == inv.trigger_type
        assert restored.status == inv.status
        assert restored.started_at == inv.started_at

    def test_get_unknown_id_raises(self, repo) -> None:
        with pytest.raises(InvestigationNotFoundError):
            repo.get(uuid.uuid4())

    def test_request_payload_preserved(self, repo) -> None:
        inv = make_investigation(request={"alert_id": "AL-002", "severity": "WARNING"})
        repo.save(inv)
        restored = repo.get(inv.investigation_id)
        assert restored.request == {"alert_id": "AL-002", "severity": "WARNING"}

    def test_status_preserved(self, repo) -> None:
        inv = make_investigation()
        inv.transition_to(InvestigationStatus.RETRIEVING)
        repo.save(inv)
        restored = repo.get(inv.investigation_id)
        assert restored.status == InvestigationStatus.RETRIEVING

    def test_outcome_preserved(self, repo) -> None:
        inv = make_investigation()
        inv.complete("SQLite adapter works.")
        repo.save(inv)
        restored = repo.get(inv.investigation_id)
        assert restored.outcome == "SQLite adapter works."
        assert restored.status == InvestigationStatus.COMPLETED


# ---------------------------------------------------------------------------
# update
# ---------------------------------------------------------------------------


class TestUpdate:
    def test_update_persists_status_change(self, repo) -> None:
        inv = make_investigation()
        repo.save(inv)
        inv.transition_to(InvestigationStatus.DIAGNOSING)
        repo.update(inv)
        restored = repo.get(inv.investigation_id)
        assert restored.status == InvestigationStatus.DIAGNOSING

    def test_update_persists_diagnosis(self, repo) -> None:
        inv = make_investigation()
        repo.save(inv)
        inv.transition_to(InvestigationStatus.DIAGNOSING)
        inv.set_diagnosis({"root_cause": "disk full", "confidence": 0.75})
        repo.update(inv)
        restored = repo.get(inv.investigation_id)
        assert restored.diagnosis == {"root_cause": "disk full", "confidence": 0.75}

    def test_update_unknown_id_raises(self, repo) -> None:
        inv = make_investigation()  # never saved
        with pytest.raises(InvestigationNotFoundError):
            repo.update(inv)


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------


class TestList:
    def test_list_empty(self, repo) -> None:
        assert repo.list() == []

    def test_list_returns_all(self, repo) -> None:
        inv1 = make_investigation()
        inv2 = make_investigation()
        repo.save(inv1)
        repo.save(inv2)
        ids = {i.investigation_id for i in repo.list()}
        assert inv1.investigation_id in ids
        assert inv2.investigation_id in ids

    def test_list_count_matches(self, repo) -> None:
        for _ in range(4):
            repo.save(make_investigation())
        assert len(repo.list()) == 4


# ---------------------------------------------------------------------------
# Serialisation with IncidentGraph
# ---------------------------------------------------------------------------


class TestGraphSerialisation:
    def test_graph_preserved_after_round_trip(self, repo) -> None:
        inv = make_investigation()
        inv.attach_graph(make_graph_with_two_nodes())
        repo.save(inv)
        restored = repo.get(inv.investigation_id)
        assert restored.incident_graph is not None
        assert len(restored.incident_graph.nodes) == 2
        assert len(restored.incident_graph.edges) == 1

    def test_graph_edge_type_preserved(self, repo) -> None:
        inv = make_investigation()
        inv.attach_graph(make_graph_with_two_nodes())
        repo.save(inv)
        restored = repo.get(inv.investigation_id)
        assert restored.incident_graph is not None
        assert restored.incident_graph.edges[0].edge_type == EdgeType.CAUSAL

    def test_none_graph_round_trips_as_none(self, repo) -> None:
        inv = make_investigation()
        repo.save(inv)
        restored = repo.get(inv.investigation_id)
        assert restored.incident_graph is None


# ---------------------------------------------------------------------------
# Serialisation with evidence list
# ---------------------------------------------------------------------------


class TestEvidenceSerialisation:
    def test_evidence_list_preserved(self, repo) -> None:
        inv = make_investigation()
        inv.add_evidence(make_event("first signal"))
        inv.add_evidence(make_event("second signal"))
        repo.save(inv)
        restored = repo.get(inv.investigation_id)
        assert len(restored.evidence) == 2

    def test_evidence_event_ids_preserved(self, repo) -> None:
        inv = make_investigation()
        event = make_event()
        inv.add_evidence(event)
        repo.save(inv)
        restored = repo.get(inv.investigation_id)
        assert restored.evidence[0].event_id == event.event_id

    def test_retrieved_evidence_preserved(self, repo) -> None:
        inv = make_investigation()
        inv.add_retrieved_evidence({"runbook_id": "rb-99", "score": 0.77})
        repo.save(inv)
        restored = repo.get(inv.investigation_id)
        assert restored.retrieved_evidence == [{"runbook_id": "rb-99", "score": 0.77}]

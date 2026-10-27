"""Tests for Investigation domain entity, InvestigationStatus, and TriggerType."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from deployd.domain.entities.core_event import CoreEvent, CoreEventType, Severity
from deployd.domain.entities.investigation import (
    IllegalStatusTransitionError,
    InvalidOperationError,
    Investigation,
    InvestigationStatus,
    TriggerType,
)
from deployd.domain.graph.graph import IncidentGraph
from pydantic import ValidationError

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

NOW = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)


def _make_investigation(**overrides) -> Investigation:
    defaults: dict = {
        "trigger_type": TriggerType.MANUAL,
        "started_at": NOW,
    }
    defaults.update(overrides)
    return Investigation(**defaults)


def _make_event() -> CoreEvent:
    return CoreEvent(
        event_type=CoreEventType.PROCESS_CRASH,
        timestamp=NOW,
        severity=Severity.CRITICAL,
        description="test crash",
    )


# ---------------------------------------------------------------------------
# InvestigationStatus enum
# ---------------------------------------------------------------------------


class TestInvestigationStatus:
    def test_all_members_defined(self) -> None:
        members = {s.value for s in InvestigationStatus}
        assert members == {
            "BUILDING",
            "RETRIEVING",
            "DIAGNOSING",
            "WAITING_FOR_EVIDENCE",
            "COMPLETED",
        }

    def test_is_str_enum(self) -> None:
        assert isinstance(InvestigationStatus.BUILDING, str)


# ---------------------------------------------------------------------------
# TriggerType enum
# ---------------------------------------------------------------------------


class TestTriggerType:
    def test_all_members_defined(self) -> None:
        members = {t.value for t in TriggerType}
        assert members == {"MANUAL", "ALERT", "SCHEDULED"}

    def test_is_str_enum(self) -> None:
        assert isinstance(TriggerType.ALERT, str)


# ---------------------------------------------------------------------------
# Investigation creation
# ---------------------------------------------------------------------------


class TestInvestigationCreation:
    def test_default_status_is_building(self) -> None:
        inv = _make_investigation()
        assert inv.status == InvestigationStatus.BUILDING

    def test_investigation_id_auto_generated(self) -> None:
        inv1 = _make_investigation()
        inv2 = _make_investigation()
        assert inv1.investigation_id != inv2.investigation_id

    def test_investigation_id_is_uuid(self) -> None:
        inv = _make_investigation()
        assert isinstance(inv.investigation_id, uuid.UUID)

    def test_custom_investigation_id_accepted(self) -> None:
        custom_id = uuid.uuid4()
        inv = _make_investigation(investigation_id=custom_id)
        assert inv.investigation_id == custom_id

    def test_started_at_converted_to_utc(self) -> None:
        cet = timezone(timedelta(hours=2))
        started = datetime(2026, 1, 1, 14, 0, 0, tzinfo=cet)
        inv = _make_investigation(started_at=started)
        assert inv.started_at.tzinfo == timezone.utc
        assert inv.started_at.hour == 12

    def test_naive_started_at_rejected(self) -> None:
        with pytest.raises(ValidationError, match="timezone-aware"):
            _make_investigation(started_at=datetime(2026, 1, 1, 12, 0, 0))

    def test_request_defaults_to_empty_dict(self) -> None:
        inv = _make_investigation()
        assert inv.request == {}

    def test_evidence_defaults_to_empty_list(self) -> None:
        inv = _make_investigation()
        assert inv.evidence == []

    def test_retrieved_evidence_defaults_to_empty_list(self) -> None:
        inv = _make_investigation()
        assert inv.retrieved_evidence == []

    def test_incident_graph_defaults_to_none(self) -> None:
        inv = _make_investigation()
        assert inv.incident_graph is None

    def test_diagnosis_defaults_to_none(self) -> None:
        inv = _make_investigation()
        assert inv.diagnosis is None

    def test_outcome_defaults_to_none(self) -> None:
        inv = _make_investigation()
        assert inv.outcome is None

    def test_invalid_trigger_type_rejected(self) -> None:
        with pytest.raises(ValidationError):
            Investigation(trigger_type="BANANA", started_at=NOW)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Status transitions
# ---------------------------------------------------------------------------


class TestStatusTransitions:
    def test_building_to_retrieving(self) -> None:
        inv = _make_investigation()
        inv.transition_to(InvestigationStatus.RETRIEVING)
        assert inv.status == InvestigationStatus.RETRIEVING

    def test_building_to_diagnosing(self) -> None:
        inv = _make_investigation()
        inv.transition_to(InvestigationStatus.DIAGNOSING)
        assert inv.status == InvestigationStatus.DIAGNOSING

    def test_building_to_completed(self) -> None:
        inv = _make_investigation()
        inv.transition_to(InvestigationStatus.COMPLETED)
        assert inv.status == InvestigationStatus.COMPLETED

    def test_retrieving_to_diagnosing(self) -> None:
        inv = _make_investigation()
        inv.transition_to(InvestigationStatus.RETRIEVING)
        inv.transition_to(InvestigationStatus.DIAGNOSING)
        assert inv.status == InvestigationStatus.DIAGNOSING

    def test_retrieving_to_waiting_for_evidence(self) -> None:
        inv = _make_investigation()
        inv.transition_to(InvestigationStatus.RETRIEVING)
        inv.transition_to(InvestigationStatus.WAITING_FOR_EVIDENCE)
        assert inv.status == InvestigationStatus.WAITING_FOR_EVIDENCE

    def test_diagnosing_to_waiting_for_evidence(self) -> None:
        inv = _make_investigation()
        inv.transition_to(InvestigationStatus.DIAGNOSING)
        inv.transition_to(InvestigationStatus.WAITING_FOR_EVIDENCE)
        assert inv.status == InvestigationStatus.WAITING_FOR_EVIDENCE

    def test_waiting_for_evidence_back_to_retrieving(self) -> None:
        inv = _make_investigation()
        inv.transition_to(InvestigationStatus.RETRIEVING)
        inv.transition_to(InvestigationStatus.WAITING_FOR_EVIDENCE)
        inv.transition_to(InvestigationStatus.RETRIEVING)
        assert inv.status == InvestigationStatus.RETRIEVING

    def test_completed_is_terminal(self) -> None:
        inv = _make_investigation()
        inv.transition_to(InvestigationStatus.COMPLETED)
        with pytest.raises(IllegalStatusTransitionError):
            inv.transition_to(InvestigationStatus.BUILDING)

    def test_building_to_waiting_is_illegal(self) -> None:
        inv = _make_investigation()
        with pytest.raises(IllegalStatusTransitionError):
            inv.transition_to(InvestigationStatus.WAITING_FOR_EVIDENCE)

    def test_illegal_transition_message_contains_states(self) -> None:
        inv = _make_investigation()
        with pytest.raises(IllegalStatusTransitionError, match="BUILDING"):
            inv.transition_to(InvestigationStatus.WAITING_FOR_EVIDENCE)


# ---------------------------------------------------------------------------
# Domain methods
# ---------------------------------------------------------------------------


class TestAttachGraph:
    def test_attach_graph_sets_field(self) -> None:
        inv = _make_investigation()
        graph = IncidentGraph()
        inv.attach_graph(graph)
        assert inv.incident_graph is graph

    def test_attach_graph_replaces_previous(self) -> None:
        inv = _make_investigation()
        g1 = IncidentGraph()
        g2 = IncidentGraph()
        inv.attach_graph(g1)
        inv.attach_graph(g2)
        assert inv.incident_graph is g2


class TestAddEvidence:
    def test_add_evidence_appends(self) -> None:
        inv = _make_investigation()
        e1 = _make_event()
        e2 = _make_event()
        inv.add_evidence(e1)
        inv.add_evidence(e2)
        assert len(inv.evidence) == 2
        assert inv.evidence[0] is e1
        assert inv.evidence[1] is e2


class TestAddRetrievedEvidence:
    def test_add_retrieved_evidence_appends(self) -> None:
        inv = _make_investigation()
        hit = {"runbook_id": "rb-1", "score": 0.92}
        inv.add_retrieved_evidence(hit)
        assert len(inv.retrieved_evidence) == 1
        assert inv.retrieved_evidence[0] == hit


class TestSetDiagnosis:
    def test_set_diagnosis_in_diagnosing_status(self) -> None:
        inv = _make_investigation()
        inv.transition_to(InvestigationStatus.DIAGNOSING)
        inv.set_diagnosis({"root_cause": "OOM killer", "confidence": 0.87})
        assert inv.diagnosis == {"root_cause": "OOM killer", "confidence": 0.87}

    def test_set_diagnosis_in_wrong_status_raises(self) -> None:
        inv = _make_investigation()  # status = BUILDING
        with pytest.raises(InvalidOperationError, match="DIAGNOSING"):
            inv.set_diagnosis({"root_cause": "disk full"})

    def test_set_diagnosis_in_retrieving_raises(self) -> None:
        inv = _make_investigation()
        inv.transition_to(InvestigationStatus.RETRIEVING)
        with pytest.raises(InvalidOperationError):
            inv.set_diagnosis({"root_cause": "timeout"})


class TestComplete:
    def test_complete_from_building_sets_outcome(self) -> None:
        inv = _make_investigation()
        inv.complete("Network partition resolved by ops team.")
        assert inv.status == InvestigationStatus.COMPLETED
        assert inv.outcome == "Network partition resolved by ops team."

    def test_complete_from_diagnosing(self) -> None:
        inv = _make_investigation()
        inv.transition_to(InvestigationStatus.DIAGNOSING)
        inv.complete("Root cause identified: misconfigured load balancer.")
        assert inv.status == InvestigationStatus.COMPLETED

    def test_complete_from_completed_raises(self) -> None:
        inv = _make_investigation()
        inv.complete("done")
        with pytest.raises(IllegalStatusTransitionError):
            inv.complete("done again")


# ---------------------------------------------------------------------------
# Mutability
# ---------------------------------------------------------------------------


class TestMutability:
    def test_investigation_is_mutable(self) -> None:
        inv = _make_investigation()
        inv.status = InvestigationStatus.RETRIEVING
        assert inv.status == InvestigationStatus.RETRIEVING

    def test_fields_can_be_updated_directly(self) -> None:
        inv = _make_investigation()
        inv.outcome = "manual override"
        assert inv.outcome == "manual override"

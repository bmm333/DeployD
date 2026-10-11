"""Incident.record_decision: the audit trail of engineer decisions (DID-38)."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from deployd.domain.entities.human_decision import DecisionValue, HumanDecision
from deployd.domain.incident.incident import Incident

T0 = datetime(2026, 10, 11, 10, 0, tzinfo=timezone.utc)


def _decision(incident_id: uuid.UUID, version: int = 1) -> HumanDecision:
    return HumanDecision(
        incident_id=incident_id,
        decision=DecisionValue.REJECT,
        engineer_id="ben",
        timestamp=T0,
        root_cause="cache growth",
        recommendation="roll back",
        comment="already rolled back",
        diagnosis_version=version,
    )


def test_decisions_are_appended_in_order() -> None:
    incident = Incident(opened_at=T0)

    incident.record_decision(_decision(incident.id, 1))
    incident.record_decision(_decision(incident.id, 2))

    assert [d.diagnosis_version for d in incident.decisions] == [1, 2]


def test_a_decision_for_another_incident_is_refused() -> None:
    incident = Incident(opened_at=T0)

    with pytest.raises(ValueError, match="another incident"):
        incident.record_decision(_decision(uuid.uuid4()))


def test_a_resolved_incident_takes_no_more_decisions() -> None:
    incident = Incident(opened_at=T0)
    incident.resolve(T0, {}, [], None)

    with pytest.raises(ValueError, match="open"):
        incident.record_decision(_decision(incident.id))

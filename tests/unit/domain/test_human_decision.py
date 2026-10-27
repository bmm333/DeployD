"""Tests for HumanDecision entity and DecisionValue enum."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from deployd.domain.entities.human_decision import DecisionValue, HumanDecision
from pydantic import ValidationError

NOW = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)


def _make_decision(**overrides) -> HumanDecision:
    defaults: dict = {
        "incident_id": uuid.uuid4(),
        "decision": DecisionValue.ACCEPT,
        "engineer_id": "eng-42",
        "timestamp": NOW,
        "root_cause": "Session cache growth exhausted the auth-service heap.",
        "recommendation": "Roll back the session cache size change.",
    }
    defaults.update(overrides)
    return HumanDecision(**defaults)


# ---------------------------------------------------------------------------
# DecisionValue enum
# ---------------------------------------------------------------------------


class TestDecisionValue:
    def test_all_members_defined(self) -> None:
        members = {d.value for d in DecisionValue}
        assert members == {"ACCEPT", "REJECT", "AMEND"}

    def test_is_str_enum(self) -> None:
        assert isinstance(DecisionValue.ACCEPT, str)


# ---------------------------------------------------------------------------
# HumanDecision creation
# ---------------------------------------------------------------------------


class TestHumanDecisionCreation:
    def test_valid_accept(self) -> None:
        d = _make_decision(decision=DecisionValue.ACCEPT)
        assert d.decision == DecisionValue.ACCEPT

    def test_valid_reject(self) -> None:
        d = _make_decision(decision=DecisionValue.REJECT, comment="We already rolled back.")
        assert d.decision == DecisionValue.REJECT

    def test_valid_amend(self) -> None:
        d = _make_decision(
            decision=DecisionValue.AMEND, amended_recommendation="Raise the heap to 2 GiB."
        )
        assert d.amended_recommendation == "Raise the heap to 2 GiB."

    def test_comment_is_optional(self) -> None:
        d = _make_decision()
        assert d.comment is None

    def test_comment_accepted(self) -> None:
        d = _make_decision(comment="Looks correct to me.")
        assert d.comment == "Looks correct to me."

    def test_diagnosis_version_defaults_to_1(self) -> None:
        d = _make_decision()
        assert d.diagnosis_version == 1

    def test_custom_diagnosis_version(self) -> None:
        d = _make_decision(diagnosis_version=3)
        assert d.diagnosis_version == 3

    def test_invalid_decision_rejected(self) -> None:
        with pytest.raises(ValidationError):
            _make_decision(decision="BANANA")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Timestamp validation
# ---------------------------------------------------------------------------


class TestTimestampValidation:
    def test_naive_timestamp_rejected(self) -> None:
        with pytest.raises(ValidationError, match="timezone-aware"):
            _make_decision(timestamp=datetime(2026, 1, 1, 12, 0, 0))

    def test_non_utc_timestamp_converted(self) -> None:
        cet = timezone(timedelta(hours=2))
        d = _make_decision(timestamp=datetime(2026, 1, 1, 14, 0, 0, tzinfo=cet))
        assert d.timestamp.tzinfo == timezone.utc
        assert d.timestamp.hour == 12


# ---------------------------------------------------------------------------
# Field validators
# ---------------------------------------------------------------------------


class TestFieldValidators:
    def test_blank_engineer_id_rejected(self) -> None:
        with pytest.raises(ValidationError, match="blank"):
            _make_decision(engineer_id="   ")

    def test_engineer_id_is_trimmed(self) -> None:
        assert _make_decision(engineer_id="  Ben  ").engineer_id == "Ben"

    @pytest.mark.parametrize("comment", [None, "   "])
    def test_rejection_needs_a_reason(self, comment: str | None) -> None:
        with pytest.raises(ValidationError, match="reason"):
            _make_decision(decision=DecisionValue.REJECT, comment=comment)

    def test_amendment_needs_the_new_recommendation(self) -> None:
        with pytest.raises(ValidationError, match="amended recommendation"):
            _make_decision(decision=DecisionValue.AMEND, amended_recommendation=" ")

    @pytest.mark.parametrize("decision", [DecisionValue.ACCEPT, DecisionValue.REJECT])
    def test_only_an_amendment_carries_a_new_recommendation(self, decision: DecisionValue) -> None:
        with pytest.raises(ValidationError, match="only an amendment"):
            _make_decision(
                decision=decision, comment="reason", amended_recommendation="something else"
            )

    def test_zero_diagnosis_version_rejected(self) -> None:
        with pytest.raises(ValidationError):
            _make_decision(diagnosis_version=0)

    def test_negative_diagnosis_version_rejected(self) -> None:
        with pytest.raises(ValidationError):
            _make_decision(diagnosis_version=-1)


# ---------------------------------------------------------------------------
# Immutability
# ---------------------------------------------------------------------------


class TestImmutability:
    def test_cannot_mutate_decision(self) -> None:
        d = _make_decision()
        with pytest.raises(ValidationError):
            d.decision = DecisionValue.REJECT  # type: ignore[misc]

    def test_cannot_mutate_engineer_id(self) -> None:
        d = _make_decision()
        with pytest.raises(ValidationError):
            d.engineer_id = "intruder"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Serialisation
# ---------------------------------------------------------------------------


class TestSerialization:
    def test_json_round_trip(self) -> None:
        d = _make_decision(
            comment="LGTM",
            diagnosis_version=2,
            evidence_references=["RB-AUTH-SERVICE-OOMKILL"],
            model="openai/gpt-oss-120b",
            prompt_version="1.3.0",
        )
        json_str = d.model_dump_json()
        restored = HumanDecision.model_validate_json(json_str)
        assert restored == d
        assert restored.incident_id == d.incident_id
        assert restored.decision == d.decision
        assert restored.engineer_id == d.engineer_id
        assert restored.comment == d.comment
        assert restored.diagnosis_version == d.diagnosis_version
        assert restored.timestamp == d.timestamp

"""Unit tests for the decision trace built after each three-tier gate decision."""

from __future__ import annotations

from typing import Any

import pytest
from deployd.application.dtos.diagnosis import DiagnosisTier
from deployd.application.dtos.retrieval import RetrievalCandidate
from deployd.entrypoints.decision_trace import build_decision_trace, reason_llm_skipped

CHAIN = ["DEPLOY_STARTED", "RESOURCE_EXHAUSTION", "PROCESS_CRASH", "HEALTH_CHECK_FAIL"]
BREAKDOWN = {
    "RB-OOM": {"semantic": 0.63, "bm25": 1.0, "causal": 0.75, "component": 1.0},
    "RB-DB": {"semantic": 0.2, "bm25": 0.1, "causal": 0.0, "component": 0.0},
}


def _trace(**overrides: Any) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "tier": DiagnosisTier.FULL,
        "component": "auth-service",
        "query": "auth-service: OOMKilled",
        "chain": CHAIN,
        "rules_fired": ["RULE-02", "RULE-04", "RULE-02", ""],
        "candidates": [RetrievalCandidate("RB-DB", 0.1), RetrievalCandidate("RB-OOM", 0.794)],
        "breakdown": BREAKDOWN,
        "threshold": 0.5,
        "agent_available": True,
        "llm_called": True,
        "tokens_used": 412,
    }
    kwargs.update(overrides)
    return build_decision_trace(**kwargs)


def test_full_tier_trace_reports_best_match_and_llm_call() -> None:
    trace = _trace()

    assert trace["tier"] == "FULL"
    assert trace["causal_chain_length"] == 3
    assert trace["rules_fired"] == ["RULE-02", "RULE-04"]
    assert trace["best_match"]["runbook_id"] == "RB-OOM"
    assert trace["best_match"]["score"] == pytest.approx(0.794)
    assert trace["best_match"]["above_threshold"] is True
    assert trace["best_match"]["score_breakdown"] == BREAKDOWN["RB-OOM"]
    assert trace["margin_to_runner_up"] == pytest.approx(0.694)
    assert trace["llm_called"] is True
    assert trace["tokens_used"] == 412
    assert trace["reason_llm_skipped"] is None


def test_chain_only_trace_explains_why_llm_was_not_called() -> None:
    trace = _trace(
        tier=DiagnosisTier.CHAIN_ONLY,
        candidates=[RetrievalCandidate("RB-OOM", 0.43)],
        llm_called=False,
        tokens_used=None,
    )

    assert trace["llm_called"] is False
    assert trace["best_match"]["above_threshold"] is False
    assert trace["margin_to_runner_up"] is None
    assert trace["reason_llm_skipped"] == (
        "no historical match above threshold (best: 0.43 < 0.50)"
    )


def test_missing_breakdown_defaults_to_zero_signals() -> None:
    trace = _trace(candidates=[RetrievalCandidate("RB-NEW", 0.9)], breakdown={})

    assert trace["best_match"]["score_breakdown"] == {
        "semantic": 0.0,
        "bm25": 0.0,
        "causal": 0.0,
        "component": 0.0,
    }


def test_top_candidates_are_capped_and_sorted() -> None:
    candidates = [RetrievalCandidate(f"RB-{i}", i / 10) for i in range(6)]
    trace = _trace(candidates=candidates, breakdown={})

    assert [c["runbook_id"] for c in trace["top_candidates"]] == ["RB-5", "RB-4", "RB-3"]


def test_empty_chain_and_no_candidates() -> None:
    trace = _trace(tier=DiagnosisTier.INCONCLUSIVE, chain=[], candidates=[], llm_called=False)

    assert trace["causal_chain_length"] == 0
    assert trace["best_match"] is None
    assert trace["top_candidates"] == []
    assert trace["reason_llm_skipped"] == "no evidence in the incident graph"


@pytest.mark.parametrize(
    ("tier", "best", "agent", "expected"),
    [
        (DiagnosisTier.CHAIN_ONLY, None, True, "no historical runbook retrieved"),
        (DiagnosisTier.FULL, 0.8, False, "gate passed but no agent is configured"),
        (DiagnosisTier.FULL, 0.8, True, None),
    ],
)
def test_reason_llm_skipped(
    tier: DiagnosisTier, best: float | None, agent: bool, expected: str | None
) -> None:
    reason = reason_llm_skipped(tier, best, 0.5, agent)
    if expected is None:
        assert reason is None
    else:
        assert reason is not None and reason.startswith(expected)

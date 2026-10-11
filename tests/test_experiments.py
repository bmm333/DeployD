from __future__ import annotations

import json

import pytest
from experiments import run_all


def test_metric_functions_compute_recall_and_mrr() -> None:
    rows = [
        {"query_id": "q1", "expected_runbook_id": "a", "ranking": ["a", "b", "c"]},
        {"query_id": "q2", "expected_runbook_id": "b", "ranking": ["c", "b", "a"]},
        {"query_id": "q3", "expected_runbook_id": None, "ranking": ["a", "b", "c"]},
    ]
    metrics = run_all._metric(rows, ["a", "b", "c"])
    assert metrics["recall_at_1"] == pytest.approx(0.5)
    assert metrics["recall_at_3"] == pytest.approx(1.0)
    assert metrics["mrr"] == pytest.approx(0.75)
    assert metrics["non_evaluable_query_ids"] == ["q3"]


def test_threshold_sweep_is_declared() -> None:
    assert [round(0.3 + i * 0.05, 2) for i in range(11)] == run_all.THRESHOLDS
    assert run_all.DEFAULT_THRESHOLD in run_all.THRESHOLDS


def test_confusion_and_precision_recall() -> None:
    cm = run_all._confusion([True, True, False, False], [True, False, True, False])

    assert cm == {"tp": 1, "tn": 1, "fp": 1, "fn": 1}
    assert run_all._prf(cm) == {"precision": 0.5, "recall": 0.5, "f1": 0.5}


def test_old_l2_score_compresses_cosine_scores() -> None:
    # Unit vectors: squared L2 = 2 - 2cos, read as a cosine distance.
    assert run_all.old_l2_score(0.9) == pytest.approx(0.8)
    assert run_all.old_l2_score(0.4) == 0.0


def test_queries_carry_no_structural_context() -> None:
    queries = run_all._query_definitions()
    scored = [q for q in queries if q["expected"] is not None]

    assert len(scored) >= 25
    assert {q["category"] for q in scored} == {"original", "paraphrase", "hard_negative"}
    assert all(
        "causal_chain" not in q and "affected_components" not in q and "events" not in q
        for q in queries
    )


def test_topology_is_what_stops_the_synthetic_false_causality() -> None:
    from deployd.adapters.outgoing.registry.json_topology import load_topology
    from deployd.application.use_cases.correlate_events import compute_incident_severity

    events = run_all.FALSE_CAUSALITY_EVENTS
    real, _ = run_all._correlate(events, load_topology(run_all.DATA / "components.json"))
    without, _ = run_all._correlate(events, run_all._complete_topology(events))

    assert compute_incident_severity(real) == "Degrading"
    assert compute_incident_severity(without) == "Critical"


def test_runner_writes_every_section(tmp_path, monkeypatch) -> None:
    metric = {"recall_at_1": 1.0, "recall_at_3": 1.0, "mrr": 1.0, "evaluated_query_count": 1}
    retrieval = {
        "status": "completed",
        "dataset": {"file": "d.json", "scored_queries": 1, "no_match_queries": 0, "runbooks": 10},
        "text_only": {"all": metric},
        "text_only_by_category": {},
        "label_structure_upper_bound": metric,
        "no_match": {"queries": [], "rejected_below_threshold": 0, "threshold": 0.5},
        "random_baseline_recall_at_3": 0.3,
        "latency_ms": {"p50": 1.0, "p95": 2.0},
    }
    gate = {
        "status": "completed",
        "notes": [],
        "scenarios": [],
        "threshold_sweep": [
            {
                "threshold": run_all.DEFAULT_THRESHOLD,
                "llm_allowed": {"precision": 1.0, "recall": 1.0, "f1": 1.0},
                "exact_decisions": 6,
            }
        ],
    }
    ops = {
        "status": "completed",
        "correlation_ms_per_event": {"samples": 1, "p50": 0.1, "p95": 0.1},
        "retrieval_ms_per_query": {"p50": 1.0, "p95": 2.0},
        "cold_start_s": {"value": 1.0, "what": "test"},
        "llm": {"status": "not measured"},
    }
    monkeypatch.setattr(run_all, "RESULTS", tmp_path)
    monkeypatch.setattr(run_all, "_build_stack", lambda directory: object())
    monkeypatch.setattr(run_all, "evaluate_retrieval", lambda stack: retrieval)
    monkeypatch.setattr(run_all, "evaluate_gate", lambda stack: gate)
    monkeypatch.setattr(run_all, "evaluate_ops", lambda stack, r: ops)

    summary = run_all.run(7)

    expected = {"retrieval", "gate", "ops", "summary"}
    assert {p.stem for p in tmp_path.glob("*.json")} == expected
    assert {p.stem for p in tmp_path.glob("*.md")} == expected
    assert summary["sections"]["gate"][1].startswith("6/6")
    assert json.loads((tmp_path / "summary.json").read_text())["seed"] == 7


def test_invalid_seed_fails_explicitly() -> None:
    with pytest.raises(ValueError, match="seed"):
        run_all.run("not-an-int")  # type: ignore[arg-type]  # the check under test

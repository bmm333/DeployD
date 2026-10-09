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


def test_runner_writes_all_sections_and_explicit_llm_skip(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(run_all, "RESULTS", tmp_path)
    retrieval = {
        "status": "completed",
        "seed": 7,
        "variants": {
            "all": {
                "queries": [],
                "metrics": {
                    "recall_at_1": None,
                    "recall_at_3": None,
                    "mrr": None,
                    "query_count": 0,
                },
            }
        },
        "latency_ms": {"unit": "ms", "samples": 0},
        "correlation_latency_ms": {"unit": "ms", "samples": 0},
        "embedding_cold_start_seconds": {"unit": "s", "samples": 1, "value": 0.1},
    }
    gate = {
        "status": "completed",
        "scenarios": [],
        "threshold_sweep": {"rows": []},
    }
    llm = {"status": "skipped", "reason": "missing key"}
    ops = {"status": "completed", "metrics": {}}
    monkeypatch.setattr(run_all, "_runbooks", lambda: [])
    monkeypatch.setattr(run_all, "evaluate_retrieval", lambda *_: retrieval)
    monkeypatch.setattr(run_all, "evaluate_gate", lambda *_: gate)
    monkeypatch.setattr(run_all, "evaluate_llm", lambda *_: llm)
    monkeypatch.setattr(run_all, "evaluate_ops", lambda *_: ops)

    run_all.run(7)

    expected = {"retrieval", "gate", "llm", "ops", "summary"}
    assert {p.stem for p in tmp_path.glob("*.json")} == expected
    assert {p.stem for p in tmp_path.glob("*.md")} == expected
    assert json.loads((tmp_path / "llm.json").read_text())["status"] == "skipped"
    assert "skipped" in (tmp_path / "llm.md").read_text()


def test_invalid_seed_fails_explicitly() -> None:
    with pytest.raises(ValueError, match="seed"):
        run_all.run("not-an-int")  # type: ignore[arg-type]


def test_query_labels_do_not_supply_structural_context() -> None:
    queries = run_all._query_definitions()
    assert queries
    assert all("causal_chain" not in q and "affected_components" not in q for q in queries)
    assert all("events" in q for q in queries)

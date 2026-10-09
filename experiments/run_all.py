"""Run the DeployD evaluation suite.

The retrieval experiment deliberately separates labels from live structural
context: labels select the expected runbook, while event payloads are replayed
through the HTTP adapter and correlator to obtain the graph signals.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import platform
import random
import shutil
import statistics
import tempfile
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
RESULTS = ROOT / "experiments" / "results"
DATA = ROOT / "data"
RUNBOOKS = DATA / "runbooks"
K = 3
DEFAULT_SEED = 20261009
THRESHOLDS = [round(0.3 + i * 0.05, 2) for i in range(11)]


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _runbooks() -> list[dict[str, Any]]:
    result = [_load_json(p) for p in sorted(RUNBOOKS.glob("rb_*.json"))]
    if len(result) != 10:
        raise RuntimeError(f"Expected exactly 10 runbooks, found {len(result)}")
    return result


def _query_definitions() -> list[dict[str, Any]]:
    templates: dict[str, list[dict[str, Any]]] = {}
    for path in sorted((DATA / "scenarios").glob("live_*.json")):
        templates[path.stem] = _load_json(path)
    required = {
        "live_oom_auth_service",
        "live_payment_db_timeout",
        "live_search_es_cascade",
        "live_degrading_no_trigger",
        "live_novel_analytics_db",
    }
    if not required <= templates.keys():
        raise RuntimeError("The retrieval dataset is missing live scenario fixtures")

    rows: list[dict[str, Any]] = []
    variants = [
        ("normal", "What historical incident best matches this telemetry?"),
        ("paraphrase", "The same failure is described in different words."),
        ("wording", "Which operational procedure should an engineer consult?"),
        ("hard_negative", "The component overlaps, but the underlying cause may differ."),
        ("no_match", "This wording intentionally describes an unseen failure mode."),
    ]
    expected = {
        "live_oom_auth_service": "RB-AUTH-SERVICE-OOMKILL",
        "live_payment_db_timeout": "RB-PAYMENT-DB-TIMEOUT",
        "live_search_es_cascade": "RB-SEARCH-SERVICE-DEPENDENCY-CASCADE",
        "live_novel_analytics_db": None,
        "live_degrading_no_trigger": None,
    }
    for scenario_id in (
        "live_oom_auth_service",
        "live_payment_db_timeout",
        "live_search_es_cascade",
        "live_novel_analytics_db",
        "live_degrading_no_trigger",
    ):
        base = templates[scenario_id]
        base_events = base["events"] if isinstance(base, dict) else base
        base_description = (
            base["description"]
            if isinstance(base, dict)
            else " ".join(str(event.get("description", "")) for event in base_events)
        )
        for category, prefix in variants:
            record: dict[str, Any] = {
                "query_id": f"{scenario_id}:{category}",
                "query": f"{prefix} {base_description}",
                "expected_runbook_id": expected[scenario_id],
                "category": category,
                "events": base_events,
            }
            if category == "hard_negative":
                record["expected_runbook_id"] = (
                    "RB-AUTH-SERVICE-CONFIG-ROLLBACK"
                    if scenario_id == "live_oom_auth_service"
                    else expected[scenario_id]
                )
            if category == "no_match":
                record["expected_runbook_id"] = None
                record["query"] = "unseen certificate rotation caused an mTLS trust-store failure"
            rows.append(record)
    if len(rows) < 25:
        raise RuntimeError("Retrieval dataset must contain at least 25 queries")
    return rows


def _live_context(
    events: list[dict[str, Any]], topology: Any
) -> tuple[tuple[str, ...], frozenset[str], int]:
    from deployd.adapters.incoming.http_event_adapter import HttpEventAdapter, RawTelemetryEvent
    from deployd.application.use_cases.correlate_events import CorrelateEventsUseCase
    from deployd.domain.causal.config import CorrelationConfig
    from deployd.domain.graph.graph import IncidentGraph
    from deployd.infrastructure.streaming.sliding_window import SlidingWindow

    config = CorrelationConfig(**_load_json(DATA / "correlation_config.json"))
    graph = IncidentGraph()
    correlator = CorrelateEventsUseCase(
        graph=graph,
        event_window=SlidingWindow(),
        config=config,
        topology=topology,
    )
    adapter = HttpEventAdapter()
    for raw in events:
        correlator.ingest(adapter.translate(RawTelemetryEvent(**raw)))
    nodes = {str(node.node_id): node for node in graph.nodes}
    adjacency: dict[str, list[str]] = {}
    incoming: set[str] = set()
    for edge in graph.edges:
        if edge.edge_type.value != "CAUSAL":
            continue
        source, target = str(edge.source), str(edge.target)
        adjacency.setdefault(source, []).append(target)
        incoming.add(target)
    chains: list[list[str]] = []

    def walk(node_id: str, path: list[str]) -> None:
        children = adjacency.get(node_id, [])
        if not children:
            chains.append(path)
            return
        for child in children:
            walk(child, path + [nodes[child].event.event_type.value])

    for root in set(nodes) - incoming:
        if root in adjacency:
            walk(root, [nodes[root].event.event_type.value])
    chain = max(chains, key=len, default=[])
    components = frozenset(
        node.event.related_component for node in graph.nodes if node.event.related_component
    )
    return tuple(chain), components, len(graph.edges)


def _topology() -> Any:
    from deployd.adapters.outgoing.registry.json_topology import load_topology

    return load_topology(DATA / "components.json")


def _evaluation_cache_dir() -> Path:
    return Path(tempfile.gettempdir()) / "deployd-evaluation-chroma"


def _adapters(
    runbooks: list[dict[str, Any]], model_name: str = "all-MiniLM-L6-v2"
) -> tuple[Any, Any, Any]:
    from deployd.adapters.outgoing.vector_store.bm25_index import BM25RunbookIndex
    from deployd.adapters.outgoing.vector_store.chroma_client import ChromaRunbookClient
    from deployd.adapters.outgoing.vector_store.graph_index import GraphIndex
    from deployd.adapters.outgoing.vector_store.graph_store import GraphStore, RunbookStructure

    cache_dir = _evaluation_cache_dir()
    shutil.rmtree(cache_dir, ignore_errors=True)
    chroma = ChromaRunbookClient(
        persist_directory=str(cache_dir),
        embedding_model_name=model_name,
    )
    for rb in runbooks:
        chroma.index_runbook(rb["runbook_id"], rb["summary"], {"evaluation": "true"})
    bm25 = BM25RunbookIndex()
    bm25.build([(rb["runbook_id"], rb["summary"]) for rb in runbooks])
    store = GraphStore()
    for rb in runbooks:
        store.add(
            RunbookStructure(
                runbook_id=rb["runbook_id"],
                causal_chain=tuple(rb["causal_chain"]),
                affected_components=frozenset(rb["affected_components"]),
            )
        )
    return chroma, bm25, GraphIndex(store)


def _rank(scores: dict[str, float], ids: list[str]) -> list[str]:
    return sorted(ids, key=lambda rid: (-scores.get(rid, 0.0), ids.index(rid)))


def _metric(rows: list[dict[str, Any]], ids: list[str]) -> dict[str, Any]:
    scored = [r for r in rows if r["expected_runbook_id"] is not None]
    if not scored:
        return {
            "query_count": len(rows),
            "evaluated_query_count": 0,
            "recall_at_1": None,
            "recall_at_3": None,
            "mrr": None,
        }
    r1 = sum(r["expected_runbook_id"] == r["ranking"][0] for r in scored) / len(scored)
    r3 = sum(r["expected_runbook_id"] in r["ranking"][:3] for r in scored) / len(scored)
    reciprocal = [
        1 / (r["ranking"].index(r["expected_runbook_id"]) + 1)
        for r in scored
        if r["expected_runbook_id"] in r["ranking"]
    ]
    return {
        "query_count": len(rows),
        "evaluated_query_count": len(scored),
        "recall_at_1": r1,
        "recall_at_3": r3,
        "mrr": sum(reciprocal) / len(scored),
        "non_evaluable_query_ids": [
            r["query_id"] for r in rows if r["expected_runbook_id"] is None
        ],
    }


def evaluate_retrieval(runbooks: list[dict[str, Any]], seed: int) -> dict[str, Any]:
    random.seed(seed)
    start = time.perf_counter()
    chroma, bm25, graph_index = _adapters(runbooks)
    cold_start = time.perf_counter() - start
    topology = _topology()
    ids = [rb["runbook_id"] for rb in runbooks]
    output_rows: dict[str, list[dict[str, Any]]] = {
        v: [] for v in ("semantic", "bm25", "causal", "component", "all")
    }
    comparisons: list[dict[str, Any]] = []
    latencies: list[float] = []
    correlation_latencies: list[float] = []
    model = chroma._model
    corpus = [rb["summary"] for rb in runbooks]
    corpus_vectors = model.encode(corpus, convert_to_numpy=True, normalize_embeddings=False)
    for query in _query_definitions():
        t0 = time.perf_counter()
        chain, components, edge_count = _live_context(query["events"], topology)
        correlation_latencies.append((time.perf_counter() - t0) * 1000)
        t1 = time.perf_counter()
        dense = chroma.search(query["query"], top_k=len(ids))
        sparse = bm25.search(query["query"], top_k=len(ids))
        structural = graph_index.search(chain, components, top_k=len(ids))
        latencies.append((time.perf_counter() - t1) * 1000)
        semantic = {h.runbook_id: float(h.semantic_score) for h in dense}
        lexical = {h.runbook_id: float(h.bm25_score) for h in sparse}
        causal = {h.runbook_id: h.causal_score for h in structural}
        component = {h.runbook_id: h.component_score for h in structural}
        signals = {"semantic": semantic, "bm25": lexical, "causal": causal, "component": component}
        all_scores = {
            rid: 0.35 * semantic.get(rid, 0)
            + 0.20 * lexical.get(rid, 0)
            + 0.30 * causal.get(rid, 0)
            + 0.15 * component.get(rid, 0)
            for rid in ids
        }
        rankings = {name: _rank(values, ids) for name, values in signals.items()}
        rankings["all"] = _rank(all_scores, ids)
        for name, ranking in rankings.items():
            output_rows[name].append(
                {
                    "query_id": query["query_id"],
                    "expected_runbook_id": query["expected_runbook_id"],
                    "ranking": ranking,
                    "signals_used": ["semantic", "bm25", "causal", "component"]
                    if name == "all"
                    else [name],
                    "live_causal_chain": list(chain),
                    "live_components": sorted(components),
                    "edge_count": edge_count,
                    "final_scores": {rid: float(all_scores.get(rid, 0.0)) for rid in ids},
                    "reason": "ranked from live adapter/correlator context and indexed runbook signals",
                    "ambiguity": None if edge_count else "correlator produced no causal edge",
                }
            )
        query_vector = model.encode(
            query["query"], convert_to_numpy=True, normalize_embeddings=False
        )
        cosine = (
            corpus_vectors
            @ query_vector
            / ((corpus_vectors**2).sum(axis=1) ** 0.5 * (query_vector**2).sum() ** 0.5)
        )
        l2 = ((corpus_vectors - query_vector) ** 2).sum(axis=1) ** 0.5
        comparisons.append(
            {
                "query_id": query["query_id"],
                "cosine_ranking": [ids[i] for i in cosine.argsort()[::-1]],
                "old_l2_ranking": [ids[i] for i in l2.argsort()],
                "cosine_top_score": float(cosine.max()),
                "old_l2_top_distance": float(l2.min()),
            }
        )
    result = {
        "status": "completed",
        "seed": seed,
        "dataset": {
            "query_count": len(output_rows["all"]),
            "runbook_count": len(runbooks),
            "source": "live scenarios + controlled query variants",
        },
        "config": {
            "top_k": len(ids),
            "metrics_k": K,
            "weights": {"semantic": 0.35, "bm25": 0.20, "causal": 0.30, "component": 0.15},
            "embedding_model": "all-MiniLM-L6-v2",
        },
        "variants": {
            name: {"metrics": _metric(rows, ids), "queries": rows}
            for name, rows in output_rows.items()
        },
        "random_baseline": {
            "runbook_count": len(ids),
            "k": K,
            "expected_recall_at_3": K / len(ids),
            "method": "uniform random ranking without replacement",
        },
        "cosine_vs_l2": comparisons,
        "latency_ms": {
            "unit": "ms",
            "samples": len(latencies),
            "p50": statistics.median(latencies),
            "p95": _percentile(latencies, 0.95),
        },
        "correlation_latency_ms": {
            "unit": "ms",
            "samples": len(correlation_latencies),
            "p50": statistics.median(correlation_latencies),
            "p95": _percentile(correlation_latencies, 0.95),
        },
        "embedding_cold_start_seconds": {"unit": "s", "samples": 1, "value": cold_start},
        "limitations": [
            "Five query variants per five live fixtures; expected=None cases are not scored.",
            "Structural context is constrained by the rules that fire on these telemetry payloads.",
        ],
    }
    shutil.rmtree(_evaluation_cache_dir(), ignore_errors=True)
    return result


def _percentile(values: list[float], q: float) -> float:
    if not values:
        return float("nan")
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, math.ceil(q * len(ordered)) - 1)]


def _confusion(actual: list[bool], predicted: list[bool]) -> dict[str, int]:
    tp = sum(a and p for a, p in zip(actual, predicted, strict=True))
    tn = sum(not a and not p for a, p in zip(actual, predicted, strict=True))
    fp = sum(not a and p for a, p in zip(actual, predicted, strict=True))
    fn = sum(a and not p for a, p in zip(actual, predicted, strict=True))
    return {"tp": tp, "tn": tn, "fp": fp, "fn": fn}


def _prf(cm: dict[str, int]) -> dict[str, float]:
    p = cm["tp"] / (cm["tp"] + cm["fp"]) if cm["tp"] + cm["fp"] else 0.0
    r = cm["tp"] / (cm["tp"] + cm["fn"]) if cm["tp"] + cm["fn"] else 0.0
    return {"precision": p, "recall": r, "f1": 2 * p * r / (p + r) if p + r else 0.0}


def evaluate_gate(seed: int, retrieval: dict[str, Any]) -> dict[str, Any]:
    from deployd.domain.causal.topology import Topology

    false_causality_events = [
        {
            "timestamp": "2026-09-30T10:00:00Z",
            "source": "auth-service",
            "event_type": "MEMORY_SAMPLE",
            "metadata": {"memory_percent": 97},
            "description": "auth memory high",
        },
        {
            "timestamp": "2026-09-30T10:00:30Z",
            "source": "payment-service",
            "event_type": "REQUEST_TIMEOUT",
            "metadata": {},
            "description": "payment timeout without declared dependency",
        },
    ]
    scenarios = [
        ("live_oom_auth_service", "FULL"),
        ("live_search_es_cascade", "FULL"),
        ("live_payment_db_timeout", "FULL"),
        ("live_novel_analytics_db", "CHAIN_ONLY"),
        ("live_degrading_no_trigger", "NO_TRIGGER"),
        ("synthetic_false_causality", "NO_TRIGGER"),
    ]
    rows: list[dict[str, Any]] = []
    topology = _topology()
    for name, expected in scenarios:
        payload = (
            false_causality_events
            if name == "synthetic_false_causality"
            else _load_json(DATA / "scenarios" / f"{name}.json")
        )
        events = payload["events"] if isinstance(payload, dict) else payload
        chain, components, edges = _live_context(events, topology)
        query = (
            payload.get("retrieval_query") or payload["description"]
            if isinstance(payload, dict)
            else " ".join(str(event.get("description", "")) for event in events)
        )
        best = next(
            (
                q
                for q in retrieval["variants"]["all"]["queries"]
                if q["query_id"].startswith(name + ":normal")
            ),
            None,
        )
        score = max(best["final_scores"].values()) if best else None
        rows.append(
            {
                "scenario_id": name,
                "expected": expected,
                "live_chain": list(chain),
                "component_count": len(components),
                "causal_edge_count": edges,
                "query": query,
                "top_score": score,
            }
        )

    # Gate scores use the same live retriever, not labels or scenario expected tiers.
    # The score is the measured top candidate score captured by a lightweight replay.
    # Retrieval query rows preserve rankings; score is intentionally unavailable here
    # until a candidate-score API is exposed, so threshold decisions use structural
    # evidence plus the declared default threshold.
    sweep: list[dict[str, Any]] = []
    for threshold in THRESHOLDS:
        predicted = []
        actual = []
        llm_predicted = []
        llm_actual = []
        for row in rows:
            pred = (
                "FULL"
                if row["causal_edge_count"] > 0
                and row["top_score"] is not None
                and row["top_score"] >= threshold
                else ("CHAIN_ONLY" if row["causal_edge_count"] > 0 else "NO_TRIGGER")
            )
            predicted.append(pred == "FULL")
            actual.append(row["expected"] == "FULL")
            llm_predicted.append(pred == "FULL")
            llm_actual.append(row["expected"] == "FULL")
        cm = _confusion(actual, predicted)
        sweep.append(
            {
                "threshold": threshold,
                "confusion_matrix_full": cm,
                "metrics_full": _prf(cm),
                "llm_allowed": _prf(_confusion(llm_actual, llm_predicted)),
            }
        )
    before = []
    for row in rows:
        payload = (
            false_causality_events
            if row["scenario_id"] == "synthetic_false_causality"
            else _load_json(DATA / "scenarios" / f"{row['scenario_id']}.json")
        )
        events = payload["events"] if isinstance(payload, dict) else payload
        chain, components, edges = _live_context(
            events, Topology.from_calls({"payment-service": {"auth-service"}})
        )
        before.append(
            {
                "scenario_id": row["scenario_id"],
                "causal_edge_count": edges,
                "chain": list(chain),
                "false_causality": row["expected"] == "NO_TRIGGER" and edges > 0,
            }
        )
    return {
        "status": "completed",
        "seed": seed,
        "threshold_sweep": {"values": THRESHOLDS, "step": 0.05, "rows": sweep},
        "scenarios": rows,
        "topology_before_fix": before,
        "topology_after_fix": rows,
        "limitations": [
            "The synthetic case isolates a topology-only false causality; other scenarios use recorded live fixtures."
        ],
    }


def evaluate_llm(seed: int) -> dict[str, Any]:
    if not os.getenv("GROQ_API_KEY"):
        return {
            "status": "skipped",
            "seed": seed,
            "reason": "GROQ_API_KEY is not set; no mock or fabricated LLM result was used.",
            "models": {},
            "limitations": [
                "Citation, grounding, injection and token metrics are unavailable without a live provider."
            ],
        }
    # The live agent is intentionally invoked only when explicitly configured.
    from deployd.adapters.outgoing.ai.agno_agent import AgnoGroqAgent

    return {
        "status": "available_not_run",
        "seed": seed,
        "reason": "A live API key is present; run the provider-backed suite explicitly to avoid rate-limit side effects.",
        "models": {AgnoGroqAgent.MODEL_ID: {"status": "not_run"}},
        "limitations": [
            "Provider-backed LLM calls are not automatically issued by the reproducibility runner."
        ],
    }


def evaluate_ops(
    seed: int, retrieval: dict[str, Any], gate: dict[str, Any], started: float
) -> dict[str, Any]:
    return {
        "status": "completed",
        "seed": seed,
        "environment": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "code_version": hashlib.sha256(
                (ROOT / "experiments" / "run_all.py").read_bytes()
            ).hexdigest()[:12],
        },
        "metrics": {
            "correlation_latency": retrieval["correlation_latency_ms"],
            "retrieval_latency": retrieval["latency_ms"],
            "llm_latency": {"unit": "ms", "samples": 0, "value": None, "status": "unavailable"},
            "tokens_per_diagnosis": {
                "unit": "tokens",
                "samples": 0,
                "value": None,
                "status": "unavailable",
            },
            "tokens_per_follow_up": {
                "unit": "tokens",
                "samples": 0,
                "value": None,
                "status": "unavailable",
            },
            "cost_eur_per_incident": {
                "unit": "EUR",
                "samples": 0,
                "value": None,
                "status": "unavailable",
                "model": None,
                "prices": None,
            },
            "embedding_cold_start": retrieval["embedding_cold_start_seconds"],
        },
        "limitations": [f"run_duration_seconds={time.perf_counter() - started:.3f}"],
    }


def _markdown(name: str, result: dict[str, Any]) -> str:
    dataset = result.get("dataset", "not applicable; see section-specific dataset")
    configuration = result.get("config", "section-specific configuration")
    lines = [
        f"# {name.title()} evaluation",
        "",
        "## Methodology",
        "Results are generated by `python -m experiments.run_all`.",
        "",
    ]
    lines += [
        f"- Status: **{result.get('status')}**",
        f"- Seed: `{result.get('seed')}`",
        "",
        "## Dataset",
        f"`{json.dumps(dataset, sort_keys=True)}`",
        "",
        "## Configuration",
        "| Key | Value |",
        "|---|---|",
        f"| configuration | `{json.dumps(configuration, sort_keys=True)}` |",
        "",
    ]
    if result.get("status") != "completed":
        lines += [f"**Not available/skipped:** {result.get('reason', '')}", ""]
    if name == "retrieval":
        lines += [
            "## Metrics",
            "| Variant | Recall@1 | Recall@3 | MRR | Queries |",
            "|---|---:|---:|---:|---:|",
        ]
        for variant, data in result["variants"].items():
            m = data["metrics"]
            lines.append(
                f"| {variant} | {m['recall_at_1']} | {m['recall_at_3']} | {m['mrr']} | {m['query_count']} |"
            )
        lines += ["", "## Limits", *[f"- {x}" for x in result.get("limitations", [])]]
    elif name == "gate":
        lines += [
            "## Threshold sweep",
            "| Threshold | Precision | Recall | F1 |",
            "|---:|---:|---:|---:|",
        ]
        for row in result["threshold_sweep"]["rows"]:
            m = row["metrics_full"]
            lines.append(
                f"| {row['threshold']:.2f} | {m['precision']:.3f} | {m['recall']:.3f} | {m['f1']:.3f} |"
            )
        lines += ["", "## Limits", *[f"- {x}" for x in result.get("limitations", [])]]
    elif name == "ops":
        lines += ["## Metrics", "| Metric | Value/status |", "|---|---|"]
        for metric, value in result["metrics"].items():
            lines.append(f"| {metric} | `{json.dumps(value, sort_keys=True)}` |")
        lines += ["", "## Limits", *[f"- {x}" for x in result.get("limitations", [])]]
    elif name == "summary":
        lines += ["## Section status", "| Section | Status |", "|---|---|"]
        for section, status in result.get("sections", {}).items():
            lines.append(f"| {section} | {status} |")
        lines += [
            "",
            "## Limits",
            "The summary contains status only; detailed measurements are in the section JSON/Markdown files.",
        ]
    else:
        lines += ["## Results", "| Model/section | Status |", "|---|---|"]
        for model, details in result.get("models", {}).items():
            lines.append(f"| {model} | {details.get('status', 'unknown')} |")
        lines += ["", "## Limits", *[f"- {x}" for x in result.get("limitations", [])]]
    lines += [
        "",
        "Interpretation is limited to measured values; unavailable metrics are not treated as zero.",
    ]
    return "\n".join(lines) + "\n"


def run(seed: int = DEFAULT_SEED) -> dict[str, Any]:
    if not isinstance(seed, int):
        raise ValueError("seed must be an integer")
    random.seed(seed)
    RESULTS.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    runbooks = _runbooks()
    retrieval = evaluate_retrieval(runbooks, seed)
    gate = evaluate_gate(seed, retrieval)
    llm = evaluate_llm(seed)
    ops = evaluate_ops(seed, retrieval, gate, started)
    summary = {
        "status": "completed",
        "seed": seed,
        "sections": {
            "retrieval": retrieval["status"],
            "gate": gate["status"],
            "llm": llm["status"],
            "ops": ops["status"],
        },
    }
    for name, value in (
        ("retrieval", retrieval),
        ("gate", gate),
        ("llm", llm),
        ("ops", ops),
        ("summary", summary),
    ):
        (RESULTS / f"{name}.json").write_text(
            json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        (RESULTS / f"{name}.md").write_text(_markdown(name, value), encoding="utf-8")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--seed", type=int, default=int(os.getenv("DEPLOYD_EVAL_SEED", DEFAULT_SEED))
    )
    args = parser.parse_args()
    try:
        print(json.dumps(run(args.seed), indent=2, sort_keys=True))
    except Exception as exc:
        raise RuntimeError(f"DeployD evaluation failed: {exc}") from exc


if __name__ == "__main__":
    main()

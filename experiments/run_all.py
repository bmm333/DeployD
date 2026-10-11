"""DID-36: reproducible evaluation of retrieval, the three-tier gate and operations.

    python -m experiments.run_all [--seed N]

Everything runs through the production code: HybridRetriever for retrieval, and
HTTP adapter -> correlator -> LiveInvestigation for the gate. No LLM is called:
the gate only needs a stub agent that cites the best candidate. The agent itself
is evaluated by experiments.model_selection (DID-34, ADR-011), whose committed
results the summary links. Writes experiments/results/{retrieval,gate,ops,summary}.
"""

from __future__ import annotations

import argparse
import json
import random
import shutil
import tempfile
import time
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
RESULTS = ROOT / "experiments" / "results"
DATASET = ROOT / "experiments" / "datasets" / "retrieval_queries.json"
LABELLED_QUERIES = DATA / "runbooks" / "test_queries.json"

K = 3
DEFAULT_SEED = 20261009
DEFAULT_THRESHOLD = 0.5
THRESHOLDS = [round(0.30 + i * 0.05, 2) for i in range(11)]
SIGNALS = ("semantic", "bm25", "causal", "component")

# Expected gate outcome and expected runbook for every live scenario.
LIVE_EXPECTED: dict[str, tuple[str, str | None]] = {
    "live_oom_auth_service": ("FULL", "RB-AUTH-SERVICE-OOMKILL"),
    "live_payment_db_timeout": ("FULL", "RB-PAYMENT-DB-TIMEOUT"),
    "live_search_es_cascade": ("FULL", "RB-SEARCH-SERVICE-DEPENDENCY-CASCADE"),
    "live_novel_analytics_db": ("CHAIN_ONLY", None),
    "live_degrading_no_trigger": ("NO_TRIGGER", None),
    "synthetic_false_causality": ("NO_TRIGGER", None),
}

# Three services misbehaving together; payment-service does not call auth-service,
# so the only true causal link is payment -> checkout (1 hop: no investigation).
FALSE_CAUSALITY_EVENTS = [
    {
        "timestamp": "2026-09-30T10:00:00Z",
        "source": "auth-service",
        "event_type": "MEMORY_SAMPLE",
        "metadata": {"memory_percent": 97},
        "description": "auth-service memory at 97%",
    },
    {
        "timestamp": "2026-09-30T10:00:30Z",
        "source": "payment-service",
        "event_type": "REQUEST_TIMEOUT",
        "metadata": {},
        "description": "payment-service request timeout",
    },
    {
        "timestamp": "2026-09-30T10:00:50Z",
        "source": "checkout-service",
        "event_type": "HEALTHCHECK_FAIL",
        "metadata": {},
        "description": "checkout-service health check failing",
    },
]


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _query_definitions() -> list[dict[str, Any]]:
    """Text-only queries: labels name the expected runbook, never the structure."""
    queries: list[dict[str, Any]] = _load_json(DATASET)["queries"]
    return queries


# ── Metrics (pure, unit-tested) ───────────────────────────────────────────────


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
    r3 = sum(r["expected_runbook_id"] in r["ranking"][:K] for r in scored) / len(scored)
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


def _rank(ids: Sequence[str], scores: dict[str, float]) -> list[str]:
    return sorted(ids, key=lambda rid: (-scores.get(rid, 0.0), ids.index(rid)))


def _percentile(values: Sequence[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, max(0, round(q * (len(ordered) - 1))))]


def _confusion(actual: list[bool], predicted: list[bool]) -> dict[str, int]:
    pairs = list(zip(actual, predicted, strict=True))
    return {
        "tp": sum(a and p for a, p in pairs),
        "tn": sum(not a and not p for a, p in pairs),
        "fp": sum(not a and p for a, p in pairs),
        "fn": sum(a and not p for a, p in pairs),
    }


def _prf(cm: dict[str, int]) -> dict[str, float]:
    p = cm["tp"] / (cm["tp"] + cm["fp"]) if cm["tp"] + cm["fp"] else 0.0
    r = cm["tp"] / (cm["tp"] + cm["fn"]) if cm["tp"] + cm["fn"] else 0.0
    return {"precision": p, "recall": r, "f1": 2 * p * r / (p + r) if p + r else 0.0}


def old_l2_score(cosine_score: float) -> float:
    """Semantic score before the cosine fix: on unit vectors squared L2 = 2 - 2cos,
    read as a cosine distance (1 - d), so the score was max(0, 2cos - 1)."""
    return max(0.0, 2 * cosine_score - 1)


# ── Production stack ──────────────────────────────────────────────────────────


@dataclass
class _Stack:
    chroma: Any
    bm25: Any
    graph_index: Any
    runbook_ids: list[str]
    cold_start_s: float

    def retriever(self, threshold: float = DEFAULT_THRESHOLD) -> Any:
        from deployd.adapters.outgoing.vector_store.hybrid_retriever import HybridRetriever

        return HybridRetriever(
            chroma=self.chroma,
            bm25=self.bm25,
            confidence_threshold=threshold,
            graph_index=self.graph_index,
        )


def _build_stack(directory: Path) -> _Stack:
    """The API's retrieval stack (entrypoints/api.py _get_retrieval) on a scratch store."""
    from deployd.adapters.outgoing.vector_store.bm25_index import BM25RunbookIndex
    from deployd.adapters.outgoing.vector_store.chroma_client import ChromaRunbookClient
    from deployd.adapters.outgoing.vector_store.graph_index import GraphIndex
    from deployd.adapters.outgoing.vector_store.graph_store import GraphStore, RunbookStructure
    from deployd.adapters.outgoing.vector_store.runbook_repository import JSONRunbookRepository

    started = time.perf_counter()
    runbooks = JSONRunbookRepository(DATA / "runbooks").list_all()
    chroma = ChromaRunbookClient(persist_directory=str(directory))
    for rb in runbooks:
        chroma.index_runbook(rb.runbook_id, rb.summary, {"tags": ",".join(rb.tags)})
    bm25 = BM25RunbookIndex()
    bm25.build([(rb.runbook_id, rb.summary) for rb in runbooks])
    store = GraphStore()
    for rb in runbooks:
        store.add(
            RunbookStructure(
                runbook_id=rb.runbook_id,
                causal_chain=tuple(rb.causal_chain),
                affected_components=frozenset(rb.affected_components),
            )
        )
    return _Stack(
        chroma=chroma,
        bm25=bm25,
        graph_index=GraphIndex(store),
        runbook_ids=sorted(rb.runbook_id for rb in runbooks),
        cold_start_s=time.perf_counter() - started,
    )


def _rankings(
    stack: _Stack,
    query: str,
    chain: tuple[str, ...] = (),
    components: frozenset[str] = frozenset(),
) -> tuple[dict[str, list[str]], float, float]:
    """Production ranking ('all') and one ranking per signal, from HybridRetriever's breakdown."""
    started = time.perf_counter()
    result, breakdown = stack.retriever().retrieve_scored(
        query, top_k=len(stack.runbook_ids), causal_chain=chain, components=components
    )
    latency_ms = (time.perf_counter() - started) * 1000
    rankings = {
        signal: _rank(
            stack.runbook_ids, {rid: getattr(scores, signal) for rid, scores in breakdown.items()}
        )
        for signal in SIGNALS
    }
    rankings["all"] = [c.runbook_id for c in result.candidates]
    best = result.candidates[0].score if result.candidates else 0.0
    return rankings, float(best), latency_ms


# ── Retrieval ─────────────────────────────────────────────────────────────────


def evaluate_retrieval(stack: _Stack) -> dict[str, Any]:
    labels = {q["query"]: q for q in _load_json(LABELLED_QUERIES)}
    variants: dict[str, list[dict[str, Any]]] = {"semantic": [], "bm25": [], "all": []}
    oracle: list[dict[str, Any]] = []
    no_match: list[dict[str, Any]] = []
    latencies: list[float] = []

    for q in _query_definitions():
        rankings, best, latency = _rankings(stack, q["query"])
        latencies.append(latency)
        if q["expected"] is None:
            no_match.append({"query_id": q["id"], "best_score": best, "top": rankings["all"][0]})
            continue
        for variant in variants:
            variants[variant].append(
                {
                    "query_id": q["id"],
                    "category": q["category"],
                    "expected_runbook_id": q["expected"],
                    "ranking": rankings[variant],
                    "best_score": best,
                }
            )
        label = labels.get(q["query"])
        if label:  # the pre-DID-36 setup: structure copied from the label (leakage)
            leaky, _, _ = _rankings(
                stack,
                q["query"],
                tuple(label["causal_chain"]),
                frozenset(label["affected_components"]),
            )
            oracle.append(
                {
                    "query_id": q["id"],
                    "expected_runbook_id": q["expected"],
                    "ranking": leaky["all"],
                }
            )

    ids = stack.runbook_ids
    by_category = {
        category: _metric([r for r in variants["all"] if r["category"] == category], ids)
        for category in ("original", "paraphrase", "hard_negative")
    }
    return {
        "status": "completed",
        "dataset": {
            "file": str(DATASET.relative_to(ROOT)),
            "scored_queries": len(variants["all"]),
            "no_match_queries": len(no_match),
            "runbooks": len(ids),
        },
        "text_only": {name: _metric(rows, ids) for name, rows in variants.items()},
        "text_only_by_category": by_category,
        "label_structure_upper_bound": _metric(oracle, ids),
        "no_match": {
            "queries": no_match,
            "rejected_below_threshold": sum(
                1 for r in no_match if r["best_score"] < DEFAULT_THRESHOLD
            ),
            "threshold": DEFAULT_THRESHOLD,
        },
        "random_baseline_recall_at_3": K / len(ids),
        "rows": variants,
        "latency_ms": {"p50": _percentile(latencies, 0.5), "p95": _percentile(latencies, 0.95)},
    }


# ── Live pipeline: correlation, gate, live retrieval ──────────────────────────


def _events(scenario: str) -> list[dict[str, Any]]:
    if scenario == "synthetic_false_causality":
        return FALSE_CAUSALITY_EVENTS
    events: list[dict[str, Any]] = _load_json(DATA / "scenarios" / f"{scenario}.json")
    return events


def _correlate(events: list[dict[str, Any]], topology: Any) -> tuple[Any, list[float]]:
    from deployd.adapters.incoming.http_event_adapter import HttpEventAdapter, RawTelemetryEvent
    from deployd.application.use_cases.correlate_events import CorrelateEventsUseCase
    from deployd.domain.causal.config import CorrelationConfig
    from deployd.domain.graph.graph import IncidentGraph
    from deployd.infrastructure.streaming.sliding_window import SlidingWindow

    graph = IncidentGraph()
    correlate = CorrelateEventsUseCase(
        graph=graph,
        event_window=SlidingWindow(),
        config=CorrelationConfig(**_load_json(DATA / "correlation_config.json")),
        topology=topology,
    )
    adapter = HttpEventAdapter()
    latencies = []
    for raw in events:
        started = time.perf_counter()
        correlate.ingest(adapter.translate(RawTelemetryEvent(**raw)))
        latencies.append((time.perf_counter() - started) * 1000)
    return graph, latencies


class _CitingAgent:
    """Stands in for the LLM: cites the best candidate, so a FULL decision stays FULL.
    The gate is under test here, not the model (that is experiments.model_selection)."""

    last_session_id = None

    def diagnose(self, component: str, causal_chains: Any, candidates: Any) -> Any:
        from deployd.application.dtos.diagnosis import AgentDiagnosis

        return AgentDiagnosis(
            root_cause=f"stub diagnosis for {component}",
            confidence="Low",
            reasoning="stub",
            recommendation="stub",
            evidence_references=[candidates[0].runbook_id],
        )

    def follow_up(self, session_id: str, message: str) -> Any:
        raise NotImplementedError


def _gate(stack: _Stack, graph: Any, threshold: float) -> tuple[str, Any]:
    """The API's decision: investigate only a CRITICAL incident, then the three-tier gate."""
    from deployd.application.orchestrators.investigation_orchestrator import (
        InvestigationOrchestrator,
    )
    from deployd.application.use_cases.correlate_events import compute_incident_severity
    from deployd.application.use_cases.live_investigation import LiveInvestigation

    if compute_incident_severity(graph) != "Critical":
        return "NO_TRIGGER", None
    result = LiveInvestigation(
        retriever=stack.retriever(threshold),
        orchestrator=InvestigationOrchestrator(agent=_CitingAgent()),
    ).execute(graph)
    return (result.tier.value if result else "NO_TRIGGER"), result


def _complete_topology(events: list[dict[str, Any]]) -> Any:
    """Every component calls every other: what the rules did before DID-22 (no topology)."""
    from deployd.domain.causal.topology import Topology

    components = {str(e["source"]) for e in events}
    return Topology.from_calls({c: components - {c} for c in components})


def evaluate_gate(stack: _Stack) -> dict[str, Any]:
    from deployd.adapters.outgoing.registry.json_topology import load_topology
    from deployd.adapters.outgoing.vector_store.similarity import blend
    from deployd.application.use_cases.correlate_events import compute_incident_severity

    topology = load_topology(DATA / "components.json")
    graphs = {name: _correlate(_events(name), topology)[0] for name in LIVE_EXPECTED}

    scenarios = []
    for name, (expected, expected_runbook) in LIVE_EXPECTED.items():
        decision, result = _gate(stack, graphs[name], DEFAULT_THRESHOLD)
        before_graph = _correlate(_events(name), _complete_topology(_events(name)))[0]
        before_decision, _ = _gate(stack, before_graph, DEFAULT_THRESHOLD)
        row: dict[str, Any] = {
            "scenario": name,
            "expected": expected,
            "decision": decision,
            "severity": compute_incident_severity(graphs[name]),
            "causal_edges": len(graphs[name].edges),
            "without_topology": {
                "decision": before_decision,
                "severity": compute_incident_severity(before_graph),
                "causal_edges": len(before_graph.edges),
            },
        }
        if result is not None:
            ranking = [c.runbook_id for c in result.candidates]
            row.update(
                expected_runbook=expected_runbook,
                top_candidate=ranking[0] if ranking else None,
                best_score=result.candidates[0].score if result.candidates else 0.0,
                expected_rank=ranking.index(expected_runbook) + 1
                if expected_runbook in ranking
                else None,
            )
            # Before the cosine fix (see old_l2_score): same retrieval, old semantic score.
            components = frozenset(c for c in result.chain_components if c != "?")
            dense = [
                type(h)(runbook_id=h.runbook_id, semantic_score=old_l2_score(h.semantic_score))
                for h in stack.chroma.search(result.query, top_k=len(stack.runbook_ids))
            ]
            sparse = stack.bm25.search(result.query, top_k=len(stack.runbook_ids))
            structural = stack.graph_index.search(
                current_causal_chain=result.chain,
                current_components=components,
                top_k=len(stack.runbook_ids),
            )
            fused = blend(dense, sparse, structural)
            row["before_cosine_fix"] = {
                "best_score": fused[0][2] if fused else 0.0,
                "gate_open": bool(fused and fused[0][2] >= DEFAULT_THRESHOLD),
            }
        scenarios.append(row)

    sweep = []
    for threshold in THRESHOLDS:
        decisions = {name: _gate(stack, graphs[name], threshold)[0] for name in LIVE_EXPECTED}
        actual = [LIVE_EXPECTED[n][0] == "FULL" for n in LIVE_EXPECTED]
        predicted = [decisions[n] == "FULL" for n in LIVE_EXPECTED]
        cm = _confusion(actual, predicted)
        sweep.append(
            {
                "threshold": threshold,
                "llm_allowed": {**cm, **_prf(cm)},
                "exact_decisions": sum(decisions[n] == LIVE_EXPECTED[n][0] for n in LIVE_EXPECTED),
                "decisions": decisions,
            }
        )
    return {
        "status": "completed",
        "threshold": DEFAULT_THRESHOLD,
        "scenarios": scenarios,
        "threshold_sweep": sweep,
        "notes": [
            "Trigger as in the API on develop: an investigation starts when the incident is "
            "CRITICAL (2+ hop causal chain).",
            "'without_topology' replays the same events with a topology where every component "
            "calls every other, i.e. the correlation rules before the DID-22 fix.",
            "The agent is a stub citing the best candidate: FULL means the gate let the LLM in.",
        ],
    }


# ── Operations ────────────────────────────────────────────────────────────────


def evaluate_ops(stack: _Stack, retrieval: dict[str, Any]) -> dict[str, Any]:
    from deployd.adapters.outgoing.registry.json_topology import load_topology

    topology = load_topology(DATA / "components.json")
    correlation = []
    for name in LIVE_EXPECTED:
        correlation += _correlate(_events(name), topology)[1]
    llm: dict[str, Any] = {"status": "not measured here; run python -m experiments.model_selection"}
    study = RESULTS / "model_selection.json"
    if study.exists():
        summaries = {s["model"]: s for s in _load_json(study)["summaries"]}
        default = summaries.get("openai/gpt-oss-120b")
        if default:
            llm = {
                "status": "from experiments/results/model_selection.json (DID-34)",
                "model": default["model"],
                "diagnosis_latency_s": default["diagnoses"]["latency_s"],
                "follow_up_latency_s": default["follow_ups"]["latency_s"],
                "diagnosis_tokens_in_out": default["diagnoses"]["mean_tokens_in_out"],
                "follow_up_tokens_in_out": default["follow_ups"]["mean_tokens_in_out"],
                "est_cost_usd_per_incident": default["est_cost_usd_per_incident"],
            }
    return {
        "status": "completed",
        "correlation_ms_per_event": {
            "samples": len(correlation),
            "p50": _percentile(correlation, 0.5),
            "p95": _percentile(correlation, 0.95),
        },
        "retrieval_ms_per_query": retrieval["latency_ms"],
        "cold_start_s": {
            "value": stack.cold_start_s,
            "what": "embedding model load + indexing 10 runbooks (model already in the HF cache)",
        },
        "llm": llm,
    }


# ── Markdown ──────────────────────────────────────────────────────────────────


def _pct(value: float | None) -> str:
    return "–" if value is None else f"{value:.2f}"


def _retrieval_md(r: dict[str, Any]) -> str:
    lines = [
        "# Retrieval",
        "",
        f"{r['dataset']['scored_queries']} scored text queries "
        f"(`{r['dataset']['file']}`) and {r['dataset']['no_match_queries']} with no correct "
        f"runbook, against {r['dataset']['runbooks']} runbooks. Every query goes through "
        "`HybridRetriever.retrieve_scored`; per-signal rankings come from its breakdown. No "
        "query carries structural context. Random recall@3 = "
        f"{r['random_baseline_recall_at_3']:.2f}.",
        "",
        "| Variant | Recall@1 | Recall@3 | MRR | Queries |",
        "|---|---:|---:|---:|---:|",
    ]
    rows = [(f"text only — {k}", v) for k, v in r["text_only"].items()]
    rows += [(f"hybrid, {k} queries", v) for k, v in r["text_only_by_category"].items()]
    rows.append(("labels as structure (old setup, leakage)", r["label_structure_upper_bound"]))
    for label, m in rows:
        lines.append(
            f"| {label} | {_pct(m['recall_at_1'])} | {_pct(m['recall_at_3'])} | "
            f"{_pct(m['mrr'])} | {m['evaluated_query_count']} |"
        )
    nm = r["no_match"]
    lines += [
        "",
        f"No-match queries scoring below the {nm['threshold']} threshold (correctly no strong "
        f"match): {nm['rejected_below_threshold']}/{len(nm['queries'])}.",
    ]
    return "\n".join(lines) + "\n"


def _gate_md(g: dict[str, Any]) -> str:
    lines = [
        "# Gate",
        "",
        *[f"- {n}" for n in g["notes"]],
        "",
        "| Scenario | Expected | Decision | Best score | Expected runbook rank | "
        "Without topology | Before cosine fix |",
        "|---|---|---|---:|---:|---|---|",
    ]
    for s in g["scenarios"]:
        before = s.get("before_cosine_fix")
        lines.append(
            f"| {s['scenario']} | {s['expected']} | {s['decision']} | "
            f"{_pct(s.get('best_score'))} | {s.get('expected_rank') or '–'} | "
            f"{s['without_topology']['decision']} ({s['without_topology']['severity']}) | "
            + (
                f"best {before['best_score']:.2f}, gate {'open' if before['gate_open'] else 'closed'}"
                if before
                else "–"
            )
            + " |"
        )
    lines += [
        "",
        "## Threshold sweep (LLM allowed = FULL)",
        "",
        "| Threshold | Precision | Recall | F1 | Exact decisions |",
        "|---:|---:|---:|---:|---:|",
    ]
    for row in g["threshold_sweep"]:
        m = row["llm_allowed"]
        lines.append(
            f"| {row['threshold']:.2f} | {m['precision']:.2f} | {m['recall']:.2f} | "
            f"{m['f1']:.2f} | {row['exact_decisions']}/{len(LIVE_EXPECTED)} |"
        )
    return "\n".join(lines) + "\n"


def _ops_md(o: dict[str, Any]) -> str:
    c, r, llm = o["correlation_ms_per_event"], o["retrieval_ms_per_query"], o["llm"]
    lines = [
        "# Operations",
        "",
        "| Stage | p50 | p95 |",
        "|---|---:|---:|",
        f"| Correlation (ms / event, {c['samples']} events) | {_pct(c['p50'])} | {_pct(c['p95'])} |",
        f"| Retrieval (ms / query) | {_pct(r['p50'])} | {_pct(r['p95'])} |",
    ]
    if "model" in llm:
        lines += [
            f"| LLM diagnosis, {llm['model']} (s) | {_pct(llm['diagnosis_latency_s']['p50'])} | "
            f"{_pct(llm['diagnosis_latency_s']['p95'])} |",
            f"| LLM follow-up (s) | {_pct(llm['follow_up_latency_s']['p50'])} | "
            f"{_pct(llm['follow_up_latency_s']['p95'])} |",
        ]
    lines += ["", f"Cold start: {o['cold_start_s']['value']:.1f} s ({o['cold_start_s']['what']})."]
    if "model" in llm:
        lines.append(
            f"Estimated LLM cost per incident: ${llm['est_cost_usd_per_incident']:.4f} "
            f"({llm['status']})."
        )
    else:
        lines.append(f"LLM: {llm['status']}.")
    return "\n".join(lines) + "\n"


def _summary_md(s: dict[str, Any]) -> str:
    lines = [
        "# Evaluation summary",
        "",
        f"Generated by `python -m experiments.run_all --seed {s['seed']}` on {s['date']}.",
        "",
        "| Section | Status | Headline |",
        "|---|---|---|",
    ]
    for name, (status, headline) in s["sections"].items():
        lines.append(f"| {name} | {status} | {headline} |")
    return "\n".join(lines) + "\n"


# ── Runner ────────────────────────────────────────────────────────────────────


def run(seed: int = DEFAULT_SEED) -> dict[str, Any]:
    if not isinstance(seed, int):
        raise ValueError("seed must be an integer")
    random.seed(seed)  # nothing is sampled; recorded so reruns are comparable
    RESULTS.mkdir(parents=True, exist_ok=True)
    directory = Path(tempfile.mkdtemp(prefix="deployd-eval-"))
    try:
        stack = _build_stack(directory)
        retrieval = evaluate_retrieval(stack)
        gate = evaluate_gate(stack)
        ops = evaluate_ops(stack, retrieval)
    finally:
        shutil.rmtree(directory, ignore_errors=True)

    hybrid = retrieval["text_only"]["all"]
    default = next(r for r in gate["threshold_sweep"] if r["threshold"] == DEFAULT_THRESHOLD)
    summary = {
        "status": "completed",
        "seed": seed,
        "date": date.today().isoformat(),
        "sections": {
            "retrieval": (
                retrieval["status"],
                f"text-only hybrid recall@3 {_pct(hybrid['recall_at_3'])} on "
                f"{hybrid['evaluated_query_count']} queries "
                f"(labels as structure: {_pct(retrieval['label_structure_upper_bound']['recall_at_3'])})",
            ),
            "gate": (
                gate["status"],
                f"{default['exact_decisions']}/{len(LIVE_EXPECTED)} decisions correct at "
                f"threshold {DEFAULT_THRESHOLD}",
            ),
            "ops": (ops["status"], f"LLM: {ops['llm']['status']}"),
            "llm": (
                "see model_selection.md",
                "ADR-011: citations, scope refusal, injection, latency, cost per model",
            ),
        },
    }
    for name, value, md in (
        ("retrieval", retrieval, _retrieval_md),
        ("gate", gate, _gate_md),
        ("ops", ops, _ops_md),
        ("summary", summary, _summary_md),
    ):
        (RESULTS / f"{name}.json").write_text(
            json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        (RESULTS / f"{name}.md").write_text(md(value), encoding="utf-8")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    print(json.dumps(run(parser.parse_args().seed), indent=2))


if __name__ == "__main__":
    main()

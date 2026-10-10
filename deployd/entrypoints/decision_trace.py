"""Decision trace: a JSON-ready explanation of one three-tier gate decision.

Pure functions only — no I/O, no LLM.  The API builds the trace right after
``InvestigationOrchestrator.run()`` so the UI can show *why* the LLM was or
was not called: the causal chain found, the rules that produced it, the best
historical match against the threshold, and the resulting tier.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from deployd.application.dtos.diagnosis import DiagnosisTier
from deployd.application.dtos.retrieval import RetrievalCandidate

_SIGNALS = ("semantic", "bm25", "causal", "component")
TOP_CANDIDATES = 3


def reason_llm_skipped(
    tier: DiagnosisTier,
    best_score: float | None,
    threshold: float,
    agent_available: bool,
) -> str | None:
    """Explain why the gate kept the LLM out, or ``None`` when it was allowed in."""
    if tier is DiagnosisTier.INCONCLUSIVE:
        return "no evidence in the incident graph"
    if tier is DiagnosisTier.CHAIN_ONLY:
        if best_score is None:
            return "no historical runbook retrieved"
        return f"no historical match above threshold (best: {best_score:.2f} < {threshold:.2f})"
    if not agent_available:
        return "gate passed but no agent is configured (GROQ_API_KEY not set)"
    return None


def build_decision_trace(
    *,
    tier: DiagnosisTier,
    component: str,
    query: str,
    chain: Sequence[str],
    rules_fired: Sequence[str],
    candidates: Sequence[RetrievalCandidate],
    breakdown: Mapping[str, Mapping[str, float]],
    threshold: float,
    agent_available: bool,
    llm_called: bool,
    tokens_used: int | None,
    llm_error: str | None = None,
    prompt_version: str | None = None,
    answer_discarded: str | None = None,
    model: str | None = None,
    citations_removed: Sequence[str] = (),
) -> dict[str, Any]:
    """Assemble the decision trace exposed by ``GET /api/v1/state``.

    ``chain`` is the ordered event types of the longest causal chain, so its
    hop count is ``len(chain) - 1``.  ``breakdown`` maps runbook IDs to their
    per-signal scores (semantic, bm25, causal, component).  ``answer_discarded``
    says why a Tier-3 answer was withheld from the engineer (fail-closed);
    ``citations_removed`` lists what the evidence validator stripped from it.
    """
    ranked = sorted(candidates, key=lambda c: c.score, reverse=True)
    top = [_candidate(c, breakdown, threshold) for c in ranked[:TOP_CANDIDATES]]
    best = top[0] if top else None
    margin = round(ranked[0].score - ranked[1].score, 3) if len(ranked) >= 2 else None
    return {
        "tier": tier.value,
        "component": component,
        "retrieval_query": query,
        "causal_chain": list(chain),
        "causal_chain_length": max(len(chain) - 1, 0),
        "rules_fired": _unique(rules_fired),
        "threshold": threshold,
        "best_match": best,
        "top_candidates": top,
        "margin_to_runner_up": margin,
        "llm_called": llm_called,
        "tokens_used": tokens_used,
        "llm_error": llm_error,
        "prompt_version": prompt_version if llm_called else None,
        "model": model if llm_called else None,
        "citations_removed": list(citations_removed) if llm_called else [],
        "answer_discarded": answer_discarded,
        "reason_llm_skipped": reason_llm_skipped(
            tier, ranked[0].score if ranked else None, threshold, agent_available
        ),
    }


def _candidate(
    candidate: RetrievalCandidate,
    breakdown: Mapping[str, Mapping[str, float]],
    threshold: float,
) -> dict[str, Any]:
    scores = breakdown.get(candidate.runbook_id, {})
    return {
        "runbook_id": candidate.runbook_id,
        "score": round(float(candidate.score), 3),
        "threshold": threshold,
        "above_threshold": bool(candidate.score >= threshold),
        "score_breakdown": {s: round(float(scores.get(s, 0.0)), 3) for s in _SIGNALS},
    }


def _unique(items: Sequence[str]) -> list[str]:
    """Deduplicate while keeping first-seen order (a rule can fire on several hops)."""
    return list(dict.fromkeys(i for i in items if i))

"""
demo/scenarios.py — Simulated incident scenarios for the DID-UI demo.

Each factory function builds a self-contained ScenarioSnapshot that exercises
one of the three InvestigationOrchestrator tiers.

    S1  crash_loop_scenario()          → Tier 3  FULL
    S2  silent_degradation_scenario()  → Tier 2  CHAIN_ONLY
    S3  false_alarm_scenario()         → Tier 1  INCONCLUSIVE

No external infra or credentials required: retrieval results are static,
and the AI agent is replaced by StubAgent.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

from deployd.application.dtos.investigation_request import InvestigationRequest
from deployd.application.dtos.retrieval import RetrievalCandidate, RetrievalResult
from deployd.domain.entities.core_event import CoreEvent, CoreEventType, Severity
from deployd.domain.graph.edge import GraphEdge
from deployd.domain.graph.edge_type import EdgeType
from deployd.domain.graph.graph import IncidentGraph
from deployd.domain.graph.node import GraphNode
from deployd.domain.health.process_health import ProcessHealthFSM, Transition

if TYPE_CHECKING:
    from collections.abc import Callable

    from deployd.domain.health.process_state import ProcessHealthStatus

# ---------------------------------------------------------------------------
# Stub AgentPort — deterministic, no LLM required
# ---------------------------------------------------------------------------


class StubAgent:
    """
    Offline stub that satisfies the AgentPort protocol.

    Returns a hard-coded, scenario-specific diagnosis string so the demo
    works without Agno credentials.  The text is deliberately labelled
    [STUB] so it is obvious in the UI that this is simulated.
    """

    def diagnose(
        self,
        component: str,
        causal_chains: list[list[GraphNode]],
        candidates: list[RetrievalCandidate],
    ) -> str:
        chain_len = sum(len(c) for c in causal_chains)
        best = candidates[0] if candidates else None
        rb_ref = f"runbook {best.runbook_id} (score {best.score:.2f})" if best else "no runbook"
        return (
            f"[STUB – Tier 3 Grounded Diagnosis]\n\n"
            f"Component **{component}** shows a causal chain of {chain_len} events "
            f"consistent with a deploy-triggered crash loop. "
            f"Historical {rb_ref} documents an identical root cause: a regression "
            f"shipped in the latest deploy caused repeated process crashes. "
            f"Recommended action: roll back the deployment and verify the fix "
            f"with a staged re-deploy before promoting to production.\n\n"
            f"⚠ Human approval required before any remediation action is executed."
        )


# ---------------------------------------------------------------------------
# ScenarioSnapshot — returned by every factory
# ---------------------------------------------------------------------------


@dataclass
class RunbookDetail:
    """Human-readable runbook metadata for display in the retrieval panel."""

    runbook_id: str
    summary: str
    root_cause: str
    fix: str
    fix_commands: list[str]
    score: float
    tags: list[str] = field(default_factory=list)


@dataclass
class ScenarioSnapshot:
    """Everything the UI needs to render one complete investigation."""

    name: str
    description: str
    component: str
    events: list[CoreEvent]
    graph: IncidentGraph
    fsm_transitions: list[Transition]
    fsm_final_state: ProcessHealthStatus
    retrieval_result: RetrievalResult
    runbook_details: list[RunbookDetail]
    request: InvestigationRequest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_BASE_TS = datetime(2026, 9, 5, 10, 0, 0, tzinfo=timezone.utc)


def _ts(offset_seconds: int) -> datetime:
    return _BASE_TS + timedelta(seconds=offset_seconds)


def _add_causal_edge(
    graph: IncidentGraph,
    source: GraphNode,
    target: GraphNode,
    confidence: float = 0.9,
    rule_id: str = "R-DEPLOY-CRASH",
) -> None:
    graph.add_edge(
        GraphEdge(
            source=source.node_id,
            target=target.node_id,
            edge_type=EdgeType.CAUSAL,
            confidence=confidence,
            rule_id=rule_id,
        )
    )


def _run_fsm(
    events: list[CoreEvent],
    max_restart_count: int = 3,
) -> tuple[ProcessHealthFSM, list[Transition]]:
    fsm = ProcessHealthFSM(
        recovery_window=timedelta(minutes=5),
        max_restart_count=max_restart_count,
        restart_time_window=timedelta(minutes=10),
    )
    for event in events:
        fsm.process_event(event)
    return fsm, fsm.transition_history


# ---------------------------------------------------------------------------
# S1 — Crash Loop (Tier 3 FULL)
# ---------------------------------------------------------------------------


def crash_loop_scenario() -> ScenarioSnapshot:
    """
    notification-service suffers a crash loop after a bad deploy.

    Timeline:
      T+0s   DEPLOY_STARTED       (INFO)
      T+30s  PROCESS_CRASH        (CRITICAL)  — first crash
      T+65s  STATE_CHANGE/restart (INFO)       — restart attempt 1
      T+90s  PROCESS_CRASH        (CRITICAL)  — second crash
      T+120s STATE_CHANGE/restart (INFO)       — restart attempt 2
      T+150s PROCESS_CRASH        (CRITICAL)  — third crash
      T+180s STATE_CHANGE/restart (INFO)       — restart attempt 3

    FSM path: HEALTHY → CRASHING → RESTARTING → CRASHING →
              RESTARTING → CRASHING → RESTARTING → CRASH_LOOP

    Retrieval: strong match (score 0.87) against RB-NOTIFICATION-CRASH-LOOP
    Tier: FULL (agent called via StubAgent)
    """
    component = "notification-service"

    e0 = CoreEvent(
        event_type=CoreEventType.DEPLOY_STARTED,
        timestamp=_ts(0),
        severity=Severity.INFO,
        related_component=component,
        description="Deploy v2.4.1 started for notification-service.",
    )
    e1 = CoreEvent(
        event_type=CoreEventType.PROCESS_CRASH,
        timestamp=_ts(30),
        severity=Severity.CRITICAL,
        related_component=component,
        description="notification-service crashed: NullPointerException in email template renderer.",
        metadata={"exit_code": 139, "signal": "SIGSEGV"},
    )
    e2 = CoreEvent(
        event_type=CoreEventType.STATE_CHANGE,
        timestamp=_ts(65),
        severity=Severity.INFO,
        related_component=component,
        description="Restart attempt 1 initiated by supervisor.",
        metadata={"is_restart": True},
    )
    e3 = CoreEvent(
        event_type=CoreEventType.PROCESS_CRASH,
        timestamp=_ts(90),
        severity=Severity.CRITICAL,
        related_component=component,
        description="notification-service crashed again on restart.",
        metadata={"exit_code": 139, "signal": "SIGSEGV"},
    )
    e4 = CoreEvent(
        event_type=CoreEventType.STATE_CHANGE,
        timestamp=_ts(120),
        severity=Severity.INFO,
        related_component=component,
        description="Restart attempt 2 initiated by supervisor.",
        metadata={"is_restart": True},
    )
    e5 = CoreEvent(
        event_type=CoreEventType.PROCESS_CRASH,
        timestamp=_ts(150),
        severity=Severity.CRITICAL,
        related_component=component,
        description="notification-service crashed a third time.",
        metadata={"exit_code": 139, "signal": "SIGSEGV"},
    )
    e6 = CoreEvent(
        event_type=CoreEventType.STATE_CHANGE,
        timestamp=_ts(180),
        severity=Severity.INFO,
        related_component=component,
        description="Restart attempt 3 initiated — crash loop threshold reached.",
        metadata={"is_restart": True},
    )

    events = [e0, e1, e2, e3, e4, e5, e6]

    # Build graph
    graph = IncidentGraph()
    nodes = [GraphNode(event=e) for e in events]
    for node in nodes:
        graph.add_node(node)

    # Causal edges: deploy → each crash; each crash → restart
    _add_causal_edge(graph, nodes[0], nodes[1], confidence=0.95, rule_id="R-DEPLOY-CRASH")
    _add_causal_edge(graph, nodes[1], nodes[2], confidence=0.90, rule_id="R-CRASH-RESTART")
    _add_causal_edge(graph, nodes[2], nodes[3], confidence=0.88, rule_id="R-RESTART-CRASH")
    _add_causal_edge(graph, nodes[3], nodes[4], confidence=0.90, rule_id="R-CRASH-RESTART")
    _add_causal_edge(graph, nodes[4], nodes[5], confidence=0.88, rule_id="R-RESTART-CRASH")
    _add_causal_edge(graph, nodes[5], nodes[6], confidence=0.90, rule_id="R-CRASH-RESTART")

    fsm, transitions = _run_fsm(events, max_restart_count=2)

    # Retrieval: strong match
    candidates = [
        RetrievalCandidate(
            runbook_id="RB-NOTIFICATION-CRASH-LOOP",
            score=0.87,
            reference_url="https://wiki.internal/runbooks/notification-crash-loop",
        ),
        RetrievalCandidate(
            runbook_id="RB-BILLING-DEPLOY-REGRESSION",
            score=0.61,
            reference_url="https://wiki.internal/runbooks/billing-deploy-regression",
        ),
    ]
    retrieval_result = RetrievalResult(candidates=candidates, confidence_threshold=0.60)

    runbook_details = [
        RunbookDetail(
            runbook_id="RB-NOTIFICATION-CRASH-LOOP",
            summary="notification-service entered a crash loop after a deploy introduced a null pointer dereference.",
            root_cause="Deploy shipped code that dereferenced an optional template field without a null check.",
            fix="Rolled back the deploy and added a null check plus a unit test.",
            fix_commands=["kubectl rollout undo deployment/notification-service -n prod"],
            score=0.87,
            tags=["crash-loop", "null-pointer", "deploy-regression"],
        ),
        RunbookDetail(
            runbook_id="RB-BILLING-DEPLOY-REGRESSION",
            summary="billing-service deploy introduced a regression causing repeated OOM kills.",
            root_cause="Memory leak in new invoice-rendering code path.",
            fix="Reverted to previous image; patched memory leak in feature branch.",
            fix_commands=["kubectl rollout undo deployment/billing-service -n prod"],
            score=0.61,
            tags=["oom", "billing", "deploy-regression"],
        ),
    ]

    request = InvestigationRequest(
        component=component,
        graph=graph,
        fsm_state=fsm.state,
        retrieval_result=retrieval_result,
    )

    return ScenarioSnapshot(
        name="S1 – Crash Loop (Tier 3: Full Diagnosis)",
        description=(
            "notification-service crashed 3× within 3 minutes after deploy v2.4.1. "
            "The FSM entered CRASH_LOOP. A strong historical match was found in the "
            "runbook store. The AI agent produces a grounded diagnosis."
        ),
        component=component,
        events=events,
        graph=graph,
        fsm_transitions=transitions,
        fsm_final_state=fsm.state,
        retrieval_result=retrieval_result,
        runbook_details=runbook_details,
        request=request,
    )


# ---------------------------------------------------------------------------
# S2 — Silent Degradation (Tier 2 CHAIN_ONLY)
# ---------------------------------------------------------------------------


def silent_degradation_scenario() -> ScenarioSnapshot:
    """
    inventory-service experiences resource exhaustion then a dependency failure.

    Timeline:
      T+0s   RESOURCE_EXHAUSTION  (WARNING)   — disk I/O saturation
      T+45s  HEALTH_CHECK_FAIL    (WARNING)   — liveness probe fails
      T+90s  DEPENDENCY_FAILURE   (CRITICAL)  — downstream DB unreachable

    FSM path: HEALTHY → DEGRADED → CRASHING
    Retrieval: best score 0.41 — below threshold 0.60
    Tier: CHAIN_ONLY (no agent called)
    """
    component = "inventory-service"

    e0 = CoreEvent(
        event_type=CoreEventType.RESOURCE_EXHAUSTION,
        timestamp=_ts(0),
        severity=Severity.WARNING,
        related_component=component,
        description="Disk I/O saturation detected on inventory-service (>95% utilisation).",
        metadata={"metric": "disk_io_util", "value": 97.4},
    )
    e1 = CoreEvent(
        event_type=CoreEventType.HEALTH_CHECK_FAIL,
        timestamp=_ts(45),
        severity=Severity.WARNING,
        related_component=component,
        description="Liveness probe failed — /health returned 503.",
        metadata={"endpoint": "/health", "status_code": 503},
    )
    e2 = CoreEvent(
        event_type=CoreEventType.DEPENDENCY_FAILURE,
        timestamp=_ts(90),
        severity=Severity.CRITICAL,
        related_component=component,
        description="inventory-db connection pool exhausted; all queries timing out.",
        metadata={"dependency": "inventory-db", "timeout_ms": 30000},
    )

    events = [e0, e1, e2]

    graph = IncidentGraph()
    nodes = [GraphNode(event=e) for e in events]
    for node in nodes:
        graph.add_node(node)

    _add_causal_edge(graph, nodes[0], nodes[1], confidence=0.75, rule_id="R-RESOURCE-HEALTH")
    _add_causal_edge(graph, nodes[1], nodes[2], confidence=0.82, rule_id="R-HEALTH-DEP-FAIL")

    fsm, transitions = _run_fsm(events)

    # Retrieval: no strong match (all scores below threshold)
    candidates = [
        RetrievalCandidate(
            runbook_id="RB-INVENTORY-DISK-FULL",
            score=0.41,
            reference_url="https://wiki.internal/runbooks/inventory-disk-full",
        ),
        RetrievalCandidate(
            runbook_id="RB-PAYMENT-DB-TIMEOUT",
            score=0.29,
            reference_url="https://wiki.internal/runbooks/payment-db-timeout",
        ),
    ]
    retrieval_result = RetrievalResult(candidates=candidates, confidence_threshold=0.60)

    runbook_details = [
        RunbookDetail(
            runbook_id="RB-INVENTORY-DISK-FULL",
            summary="inventory-service ran out of disk space due to unbounded write-ahead log growth.",
            root_cause="WAL archiving disabled; log files filled the volume over 72 h.",
            fix="Cleared stale WAL files and re-enabled archiving with a retention policy.",
            fix_commands=[
                "find /var/lib/inventory/wal -mtime +1 -delete",
                "systemctl restart inventory-service",
            ],
            score=0.41,
            tags=["disk", "wal", "inventory"],
        ),
        RunbookDetail(
            runbook_id="RB-PAYMENT-DB-TIMEOUT",
            summary="payment-service DB connection pool exhausted during a traffic spike.",
            root_cause="Pool size capped at 10; not sufficient for peak load.",
            fix="Increased pool size to 50 and added circuit-breaker.",
            fix_commands=["kubectl set env deployment/payment-service DB_POOL_SIZE=50 -n prod"],
            score=0.29,
            tags=["db", "pool", "payment"],
        ),
    ]

    request = InvestigationRequest(
        component=component,
        graph=graph,
        fsm_state=fsm.state,
        retrieval_result=retrieval_result,
    )

    return ScenarioSnapshot(
        name="S2 – Silent Degradation (Tier 2: Chain Only)",
        description=(
            "inventory-service showed disk I/O saturation, health-check failures, and "
            "ultimately a dependency failure — all within 90 seconds. No historical "
            "runbook matched above the confidence threshold. Human review required."
        ),
        component=component,
        events=events,
        graph=graph,
        fsm_transitions=transitions,
        fsm_final_state=fsm.state,
        retrieval_result=retrieval_result,
        runbook_details=runbook_details,
        request=request,
    )


# ---------------------------------------------------------------------------
# S3 — False Alarm (Tier 1 INCONCLUSIVE)
# ---------------------------------------------------------------------------


def false_alarm_scenario() -> ScenarioSnapshot:
    """
    scheduler-service: no events observed; FSM stays HEALTHY.

    Timeline: empty event stream
    FSM path: HEALTHY (no transitions)
    Retrieval: empty (no query made)
    Tier: INCONCLUSIVE (no agent called)
    """
    component = "scheduler-service"

    events: list[CoreEvent] = []
    graph = IncidentGraph()

    fsm = ProcessHealthFSM(
        recovery_window=timedelta(minutes=5),
        max_restart_count=3,
        restart_time_window=timedelta(minutes=10),
    )
    transitions: list[Transition] = []

    retrieval_result = RetrievalResult(candidates=[], confidence_threshold=0.60)
    runbook_details: list[RunbookDetail] = []

    request = InvestigationRequest(
        component=component,
        graph=graph,
        fsm_state=fsm.state,
        retrieval_result=retrieval_result,
    )

    return ScenarioSnapshot(
        name="S3 – False Alarm (Tier 1: Inconclusive)",
        description=(
            "scheduler-service was flagged by an external alert, but no CoreEvents "
            "were recorded in the query window and the FSM reports HEALTHY. "
            "Insufficient evidence to diagnose a problem — human verification required."
        ),
        component=component,
        events=events,
        graph=graph,
        fsm_transitions=transitions,
        fsm_final_state=fsm.state,
        retrieval_result=retrieval_result,
        runbook_details=runbook_details,
        request=request,
    )


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

SCENARIOS: dict[str, Callable[[], ScenarioSnapshot]] = {
    "S1 – Crash Loop (Tier 3: Full)": crash_loop_scenario,
    "S2 – Silent Degradation (Tier 2: Chain Only)": silent_degradation_scenario,
    "S3 – False Alarm (Tier 1: Inconclusive)": false_alarm_scenario,
}

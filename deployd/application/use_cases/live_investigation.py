"""Use case: Live Investigation."""

from __future__ import annotations

import logging
from datetime import timedelta

from deployd.application.dtos.diagnosis import DiagnosisTier
from deployd.application.dtos.investigation_request import InvestigationRequest
from deployd.application.dtos.live_investigation_result import LiveInvestigationResult
from deployd.application.orchestrators.investigation_orchestrator import InvestigationOrchestrator
from deployd.application.ports.retrieval_port import RetrievalPort
from deployd.domain.causal.causal_engine import CausalEngine
from deployd.domain.graph.graph import IncidentGraph
from deployd.domain.graph.node import GraphNode
from deployd.domain.health.process_health import ProcessHealthFSM
from deployd.domain.health.process_state import ProcessHealthStatus

log = logging.getLogger(__name__)


class LiveInvestigation:
    """Use case: run an investigation from the live incident graph."""

    def __init__(
        self,
        retriever: RetrievalPort,
        orchestrator: InvestigationOrchestrator,
        fsm_recovery_window_s: int = 300,
        fsm_max_restarts: int = 3,
        fsm_restart_window_s: int = 120,
    ) -> None:
        self._retriever = retriever
        self._orchestrator = orchestrator
        self._fsm_recovery_window_s = fsm_recovery_window_s
        self._fsm_max_restarts = fsm_max_restarts
        self._fsm_restart_window_s = fsm_restart_window_s

    def execute(self, graph: IncidentGraph) -> LiveInvestigationResult | None:
        """Run the investigation and return the result, or None if no chain exists."""
        chain = self._longest_chain(graph)
        if not chain:
            return None

        component = chain[0].event.related_component or "unknown-component"
        chain_types = tuple(n.event.event_type.value for n in chain)
        components = frozenset(
            n.event.related_component for n in chain if n.event.related_component
        )
        query = f"{component}: " + "; ".join(
            n.event.description or n.event.event_type.value for n in chain
        )

        fsm_state_val = self._fsm_state(graph, component)
        rules_fired_val = tuple(self._chain_rules(graph, chain))

        retrieval, breakdown = self._retriever.retrieve_scored(
            query, causal_chain=chain_types, components=components
        )

        request = InvestigationRequest(
            component=component,
            graph=graph,
            fsm_state=fsm_state_val,
            retrieval_result=retrieval,
        )

        agent_available = self._orchestrator._agent is not None
        llm_error = None
        tier = DiagnosisTier.INCONCLUSIVE
        diagnosis = None
        llm_usage = None
        result = None

        try:
            result = self._orchestrator.run(request)
            tier = result.tier
            diagnosis = result.structured_diagnosis
            if tier is DiagnosisTier.FULL:
                llm_usage = self._orchestrator.last_token_usage
        except (RuntimeError, ValueError, TypeError) as exc:
            log.exception("Tier-3 agent run failed")
            tier = DiagnosisTier.FULL
            llm_error = str(exc) if agent_available else None

        summary = (
            result.remediation.summary
            if result
            else (
                "The gate allowed a grounded diagnosis, but the agent could not produce a "
                "validated answer. Showing the deterministic evidence instead."
            )
        )

        import dataclasses

        return LiveInvestigationResult(
            component=component,
            query=query,
            tier=tier,
            chain=chain_types,
            rules_fired=rules_fired_val,
            candidates=retrieval.candidates,
            breakdown={
                rid: dataclasses.asdict(scores) if dataclasses.is_dataclass(scores) else scores
                for rid, scores in breakdown.items()
            },
            diagnosis=diagnosis,
            llm_usage=llm_usage,
            llm_error=llm_error,
            agent_available=agent_available,
            summary=summary,
        )

    def _longest_chain(self, graph: IncidentGraph) -> list[GraphNode]:
        engine = CausalEngine(graph)
        chains = [c for root in graph.get_root_nodes() for c in engine.causal_chain(root.node_id)]
        return max(chains, key=len, default=[])

    def _chain_rules(self, graph: IncidentGraph, chain: list[GraphNode]) -> list[str]:
        rules: list[str] = []
        for src, dst in zip(chain, chain[1:], strict=False):
            edge = next(
                (e for e in graph.outgoing_edges(src.node_id) if e.target == dst.node_id), None
            )
            if edge is not None:
                rules.append(edge.rule_id or edge.edge_type.value)
        return rules

    def _fsm_state(self, graph: IncidentGraph, component: str) -> ProcessHealthStatus:
        fsm = ProcessHealthFSM(
            recovery_window=timedelta(seconds=self._fsm_recovery_window_s),
            max_restart_count=self._fsm_max_restarts,
            restart_time_window=timedelta(seconds=self._fsm_restart_window_s),
        )
        events = sorted(
            (n.event for n in graph.nodes if n.event.related_component == component),
            key=lambda e: e.timestamp,
        )
        for event in events:
            fsm.process_event(event)
        return fsm.state

import uuid
from datetime import timedelta
from typing import TYPE_CHECKING

from deployd.application.dtos.investigation_request import InvestigationRequest
from deployd.application.use_cases.retrieve_candidates import RetrieveCandidates
from deployd.domain.entities.core_event import CoreEvent, Severity
from deployd.domain.graph.edge import GraphEdge
from deployd.domain.graph.edge_type import EdgeType
from deployd.domain.graph.graph import IncidentGraph
from deployd.domain.graph.node import GraphNode
from deployd.domain.health.process_health import ProcessHealthFSM

if TYPE_CHECKING:
    from deployd.application.dtos.diagnosis import TierDiagnosisResult
    from deployd.application.orchestrators.investigation_orchestrator import (
        InvestigationOrchestrator,
    )


class BuildInvestigation:
    """Use case: Assembles an investigation from raw events and delegates to orchestrator."""

    def __init__(
        self,
        orchestrator: "InvestigationOrchestrator",
        retrieval_use_case: RetrieveCandidates,
        fsm_recovery_window_s: int = 300,
        fsm_max_restarts: int = 3,
        fsm_restart_window_s: int = 120,
    ) -> None:
        self._orchestrator = orchestrator
        self._retrieval_use_case = retrieval_use_case
        self._fsm_recovery_window_s = fsm_recovery_window_s
        self._fsm_max_restarts = fsm_max_restarts
        self._fsm_restart_window_s = fsm_restart_window_s

    def execute(self, component_name: str, events: list[CoreEvent]) -> "TierDiagnosisResult":
        fsm = ProcessHealthFSM(
            recovery_window=timedelta(seconds=self._fsm_recovery_window_s),
            max_restart_count=self._fsm_max_restarts,
            restart_time_window=timedelta(seconds=self._fsm_restart_window_s),
        )
        graph = IncidentGraph()
        node_ids: list[uuid.UUID] = []

        retrieval_query = f"{component_name}: unknown failure"

        for i, event in enumerate(events):
            if event.severity == Severity.CRITICAL and retrieval_query.endswith("unknown failure"):
                retrieval_query = f"{component_name}: {event.event_type.value}"

            node = GraphNode(event=event)
            graph.add_node(node)
            node_ids.append(node.node_id)
            fsm.process_event(event)

            if i == 0:
                continue

            causal_parent_idx = None
            raw_idx = event.metadata.get("causal_parent_index")
            if raw_idx is not None:
                causal_parent_idx = int(str(raw_idx))

            if causal_parent_idx is not None and 0 <= causal_parent_idx < i:
                parent_node_id = node_ids[causal_parent_idx]
                edge_type = EdgeType.CAUSAL
                confidence = 1.0
                rule_id = "explicit:causal_link"
            else:
                parent_node_id = node_ids[i - 1]
                edge_type = EdgeType.TEMPORAL
                confidence = 0.5
                rule_id = "explicit:temporal_sequence"

            graph.add_edge(
                GraphEdge(
                    source=parent_node_id,
                    target=node.node_id,
                    edge_type=edge_type,
                    confidence=confidence,
                    rule_id=rule_id,
                )
            )

        retrieval_result = self._retrieval_use_case.execute(query=retrieval_query)
        request = InvestigationRequest(
            component=component_name,
            graph=graph,
            fsm_state=fsm.state,
            retrieval_result=retrieval_result,
        )
        return self._orchestrator.run(request)

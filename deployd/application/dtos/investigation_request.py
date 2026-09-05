"""
DID-12: Input contract for the InvestigationOrchestrator.

Everything the orchestrator needs to make a tier decision and produce a
DiagnosisResult is carried in this single DTO.  The orchestrator itself has
no I/O; callers are responsible for building the graph, running the FSM, and
executing the retrieval query before calling orchestrator.run().
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from deployd.application.dtos.retrieval import RetrievalResult
    from deployd.domain.graph.graph import IncidentGraph
    from deployd.domain.health.process_state import ProcessHealthStatus


@dataclass(frozen=True)
class InvestigationRequest:
    """
    Input to InvestigationOrchestrator.run().

    `component`        — identifier of the service / process being investigated.
    `graph`            — IncidentGraph built from observed CoreEvents for the
                         query window; may be empty (Tier-1 path).
    `fsm_state`        — current ProcessHealthStatus of `component` after
                         replaying all events through ProcessHealthFSM.
    `retrieval_result` — output of the HybridRetriever query for this incident;
                         pass RetrievalResult() (empty, default threshold) when
                         no retrieval was performed.
    """

    component: str
    graph: IncidentGraph
    fsm_state: ProcessHealthStatus
    retrieval_result: RetrievalResult

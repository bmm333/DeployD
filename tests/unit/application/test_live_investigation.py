import pytest
from datetime import datetime, timezone
import uuid
from typing import Any

from deployd.application.use_cases.live_investigation import LiveInvestigation
from deployd.application.orchestrators.investigation_orchestrator import InvestigationOrchestrator
from deployd.application.dtos.diagnosis import AgentDiagnosis, DiagnosisTier
from deployd.application.dtos.retrieval import RetrievalResult, RetrievalCandidate
from deployd.domain.graph.graph import IncidentGraph
from deployd.domain.graph.node import GraphNode
from deployd.domain.entities.core_event import CoreEvent, CoreEventType, Severity


class StubRetriever:
    def __init__(self, result: RetrievalResult, breakdown: dict[str, Any]):
        self.result = result
        self.breakdown = breakdown
        self.confidence_threshold = result.confidence_threshold

    def retrieve_scored(self, query: str, top_k: int = 5, causal_chain: tuple[str, ...] = (), components: frozenset[str] = frozenset()):
        return self.result, self.breakdown

    def retrieve(self, query: str, top_k: int = 5, causal_chain: tuple[str, ...] = (), components: frozenset[str] = frozenset()):
        return self.result


class StubAgent:
    def __init__(self, diagnosis: AgentDiagnosis | None = None, error: bool = False):
        self.last_session_id = "test-session"
        self.last_token_usage = {"prompt": 10, "completion": 20}
        self.diagnosis = diagnosis
        self.error = error

    def diagnose(self, component: str, causal_chains: list[Any], candidates: list[Any]) -> AgentDiagnosis:
        if self.error:
            raise RuntimeError("Agent failure")
        if self.diagnosis is None:
            raise RuntimeError("No diagnosis configured")
        return self.diagnosis

    def follow_up(self, session_id: str, message: str) -> AgentDiagnosis:
        if self.diagnosis is None:
            raise RuntimeError("No diagnosis configured")
        return self.diagnosis


def create_mock_event(component: str = "web-tier", severity: Severity = Severity.CRITICAL) -> CoreEvent:
    return CoreEvent(
        event_id=uuid.uuid4(),
        timestamp=datetime.now(timezone.utc),
        event_type=CoreEventType.PROCESS_CRASH,
        severity=severity,
        description="Crash",
        related_component=component,
    )


def test_inconclusive_gate():
    # If the graph has no causal chain, execute returns None
    graph = IncidentGraph()
    retriever = StubRetriever(
        result=RetrievalResult(
            candidates=[], confidence_threshold=0.5
        ),
        breakdown={},
    )
    orchestrator = InvestigationOrchestrator(agent=None)
    use_case = LiveInvestigation(retriever, orchestrator)
    
    result = use_case.execute(graph)
    assert result is None


def test_chain_only_gate():
    # Graph with one node (chain of length 1)
    graph = IncidentGraph()
    event = create_mock_event()
    graph.add_node(GraphNode(node_id=event.event_id, event=event))

    retriever = StubRetriever(
        result=RetrievalResult(
            candidates=[], confidence_threshold=0.5
        ),
        breakdown={},
    )
    orchestrator = InvestigationOrchestrator(agent=None)
    use_case = LiveInvestigation(retriever, orchestrator)
    
    result = use_case.execute(graph)
    assert result is not None
    assert result.tier == DiagnosisTier.CHAIN_ONLY
    assert result.agent_available is False
    assert result.llm_usage is None
    assert result.llm_error is None


def test_full_gate():
    graph = IncidentGraph()
    event = create_mock_event()
    graph.add_node(GraphNode(node_id=event.event_id, event=event))

    candidate = RetrievalCandidate(runbook_id="RB-123", score=0.9)
    retriever = StubRetriever(
        result=RetrievalResult(
            candidates=[candidate], confidence_threshold=0.5
        ),
        breakdown={"RB-123": {}},
    )
    diagnosis = AgentDiagnosis(
        root_cause="Bad config",
        reasoning="Because of logs",
        recommendation="Fix config",
        confidence="High",
        evidence_references=["RB-123"]
    )
    agent = StubAgent(diagnosis=diagnosis)
    orchestrator = InvestigationOrchestrator(agent=agent)
    use_case = LiveInvestigation(retriever, orchestrator)
    
    result = use_case.execute(graph)
    assert result is not None
    assert result.tier == DiagnosisTier.FULL
    assert result.agent_available is True
    assert result.llm_usage == {"prompt": 10, "completion": 20}
    assert result.llm_error is None
    assert result.diagnosis is not None
    assert result.diagnosis.root_cause == "Bad config"


def test_agent_failure_fail_closed():
    graph = IncidentGraph()
    event = create_mock_event()
    graph.add_node(GraphNode(node_id=event.event_id, event=event))

    candidate = RetrievalCandidate(runbook_id="RB-123", score=0.9)
    retriever = StubRetriever(
        result=RetrievalResult(
            candidates=[candidate], confidence_threshold=0.5
        ),
        breakdown={"RB-123": {}},
    )
    # Agent will raise RuntimeError
    agent = StubAgent(error=True)
    orchestrator = InvestigationOrchestrator(agent=agent)
    use_case = LiveInvestigation(retriever, orchestrator)
    
    result = use_case.execute(graph)
    assert result is not None
    # Falls back to FULL tier but with llm_error and no diagnosis
    assert result.tier == DiagnosisTier.FULL
    assert result.agent_available is True
    assert result.llm_error == "Agent failure"
    assert result.diagnosis is None
    assert result.summary == "The gate allowed a grounded diagnosis, but the agent could not produce a validated answer. Showing the deterministic evidence instead."

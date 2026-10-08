import uuid
from datetime import datetime, timezone
from typing import Any

import pytest
from deployd.application.dtos.diagnosis import AgentDiagnosis, DiagnosisTier
from deployd.application.dtos.retrieval import RetrievalCandidate, RetrievalResult
from deployd.application.orchestrators.investigation_orchestrator import InvestigationOrchestrator
from deployd.application.use_cases.live_investigation import LiveInvestigation
from deployd.domain.entities.core_event import CoreEvent, CoreEventType, Severity
from deployd.domain.graph.graph import IncidentGraph
from deployd.domain.graph.node import GraphNode


class StubRetriever:
    def __init__(self, result: RetrievalResult, breakdown: dict[str, Any]):
        self.result = result
        self.breakdown = breakdown
        self.confidence_threshold = result.confidence_threshold

    def retrieve_scored(
        self,
        query: str,
        top_k: int = 5,
        causal_chain: tuple[str, ...] = (),
        components: frozenset[str] = frozenset(),
    ):
        return self.result, self.breakdown

    def retrieve(
        self,
        query: str,
        top_k: int = 5,
        causal_chain: tuple[str, ...] = (),
        components: frozenset[str] = frozenset(),
    ):
        return self.result


class StubAgent:
    def __init__(self, diagnosis: AgentDiagnosis | None = None, error: Exception | None = None):
        self.last_session_id = "test-session"
        self.last_token_usage = 30
        self.diagnosis = diagnosis
        self.error = error

    def diagnose(
        self, component: str, causal_chains: list[Any], candidates: list[Any]
    ) -> AgentDiagnosis:
        if self.error is not None:
            raise self.error
        if self.diagnosis is None:
            raise RuntimeError("No diagnosis configured")
        return self.diagnosis

    def follow_up(self, session_id: str, message: str) -> AgentDiagnosis:
        if self.diagnosis is None:
            raise RuntimeError("No diagnosis configured")
        return self.diagnosis


def create_mock_event(
    component: str = "web-tier", severity: Severity = Severity.CRITICAL
) -> CoreEvent:
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
        result=RetrievalResult(candidates=[], confidence_threshold=0.5),
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
        result=RetrievalResult(candidates=[], confidence_threshold=0.5),
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
        result=RetrievalResult(candidates=[candidate], confidence_threshold=0.5),
        breakdown={"RB-123": {}},
    )
    diagnosis = AgentDiagnosis(
        root_cause="Bad config",
        reasoning="Because of logs",
        recommendation="Fix config",
        confidence="High",
        evidence_references=["RB-123"],
    )
    agent = StubAgent(diagnosis=diagnosis)
    orchestrator = InvestigationOrchestrator(agent=agent)
    use_case = LiveInvestigation(retriever, orchestrator)

    result = use_case.execute(graph)
    assert result is not None
    assert result.tier == DiagnosisTier.FULL
    assert result.agent_available is True
    assert result.llm_usage == 30
    assert result.threshold == 0.5
    assert result.chain_components == ("web-tier",)
    assert result.llm_error is None
    assert result.diagnosis is not None
    assert result.diagnosis.root_cause == "Bad config"


def test_agent_failure_fail_closed():
    graph = IncidentGraph()
    event = create_mock_event()
    graph.add_node(GraphNode(node_id=event.event_id, event=event))

    candidate = RetrievalCandidate(runbook_id="RB-123", score=0.9)
    retriever = StubRetriever(
        result=RetrievalResult(candidates=[candidate], confidence_threshold=0.5),
        breakdown={"RB-123": {}},
    )
    agent = StubAgent(error=RuntimeError("Agent failure"))
    orchestrator = InvestigationOrchestrator(agent=agent)
    use_case = LiveInvestigation(retriever, orchestrator)

    result = use_case.execute(graph)
    assert result is not None
    # Falls back to FULL tier but with llm_error and no diagnosis
    assert result.tier == DiagnosisTier.FULL
    assert result.agent_available is True
    assert result.llm_error == "Agent failure"
    assert result.diagnosis is None
    assert (
        result.summary
        == "The gate allowed a grounded diagnosis, but the agent could not produce a validated answer. Showing the deterministic evidence instead."
    )


def _full_gate_graph() -> tuple[IncidentGraph, StubRetriever]:
    graph = IncidentGraph()
    event = create_mock_event()
    graph.add_node(GraphNode(node_id=event.event_id, event=event))
    retriever = StubRetriever(
        result=RetrievalResult(
            candidates=[RetrievalCandidate(runbook_id="RB-123", score=0.9)],
            confidence_threshold=0.5,
        ),
        breakdown={},
    )
    return graph, retriever


def test_on_start_announces_the_chain_before_the_gate_runs():
    graph, retriever = _full_gate_graph()
    calls: list[tuple[str, int]] = []
    agent = StubAgent(error=RuntimeError("Agent failure"))
    use_case = LiveInvestigation(retriever, InvestigationOrchestrator(agent=agent))

    use_case.execute(graph, on_start=lambda component, hops: calls.append((component, hops)))
    use_case.execute(IncidentGraph(), on_start=lambda component, hops: calls.append(("x", 0)))

    assert calls == [("web-tier", 0)]


def test_agent_errors_are_injected_by_the_composition_root():
    graph, retriever = _full_gate_graph()
    agent = StubAgent(error=ConnectionError("provider unreachable"))
    use_case = LiveInvestigation(
        retriever, InvestigationOrchestrator(agent=agent), agent_errors=(ConnectionError,)
    )

    result = use_case.execute(graph)

    assert result is not None
    assert result.llm_error == "provider unreachable"
    assert result.diagnosis is None


def test_unexpected_errors_are_not_reported_as_agent_failures():
    graph, retriever = _full_gate_graph()
    agent = StubAgent(error=KeyError("bug"))
    use_case = LiveInvestigation(retriever, InvestigationOrchestrator(agent=agent))

    with pytest.raises(KeyError):
        use_case.execute(graph)

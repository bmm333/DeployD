"""
DID-12: Unit tests for InvestigationOrchestrator.

Coverage target: every tier selection path + both sides of the confidence
threshold boundary.  The key invariants tested here back the "DeployD never
hallucinates" claim in the technical report (§4):

  - Tier 1: agent is NEVER called when the graph is empty.
  - Tier 2: agent is NEVER called when there is no strong RAG match;
            remediation always says "no known fix" and evidence_references == [].
  - Tier 3: agent IS called exactly once when a strong match exists.
  - Boundary (score == threshold): treated as a strong match (>=), goes Tier 3.
  - Boundary (score < threshold):  no strong match, stays Tier 2.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock, create_autospec

import pytest

from deployd.application.dtos.diagnosis import DiagnosisTier
from deployd.application.dtos.investigation_request import InvestigationRequest
from deployd.application.dtos.retrieval import RetrievalCandidate, RetrievalResult
from deployd.application.orchestrators.investigation_orchestrator import (
    AgentPort,
    InvestigationOrchestrator,
)
from deployd.domain.entities.core_event import CoreEvent, CoreEventType, Severity
from deployd.domain.graph.edge import GraphEdge
from deployd.domain.graph.edge_type import EdgeType
from deployd.domain.graph.graph import IncidentGraph
from deployd.domain.graph.node import GraphNode
from deployd.domain.health.process_state import ProcessHealthStatus

# ── Shared helpers ─────────────────────────────────────────────────────────────

_T0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
_T1 = datetime(2026, 1, 1, 12, 0, 10, tzinfo=timezone.utc)

_THRESHOLD = 0.7


def _make_event(offset_seconds: int = 0) -> CoreEvent:
    return CoreEvent(
        event_id=uuid.uuid4(),
        event_type=CoreEventType.PROCESS_CRASH,
        severity=Severity.CRITICAL,
        timestamp=datetime(2026, 1, 1, 12, 0, offset_seconds, tzinfo=timezone.utc),
        description="test event",
    )


def _make_node(offset_seconds: int = 0) -> GraphNode:
    return GraphNode(event=_make_event(offset_seconds))


def _empty_graph() -> IncidentGraph:
    return IncidentGraph()


def _single_node_graph() -> IncidentGraph:
    """Graph with one node and no edges — one root, no causal chain downstream."""
    g = IncidentGraph()
    g.add_node(_make_node(0))
    return g


def _two_node_causal_graph() -> tuple[IncidentGraph, uuid.UUID, uuid.UUID]:
    """Graph: A --CAUSAL--> B. Returns graph, id_A, id_B."""
    g = IncidentGraph()
    node_a = _make_node(0)
    node_b = _make_node(10)
    g.add_node(node_a)
    g.add_node(node_b)
    g.add_edge(
        GraphEdge(
            source=node_a.node_id,
            target=node_b.node_id,
            edge_type=EdgeType.CAUSAL,
            confidence=1.0,
        )
    )
    return g, node_a.node_id, node_b.node_id


def _stub_agent() -> AgentPort:
    """A strict autospec mock of AgentPort — any unexpected call raises."""
    agent = create_autospec(AgentPort, instance=True)
    agent.diagnose.return_value = "agent diagnosis summary"
    return agent


def _no_match_retrieval() -> RetrievalResult:
    return RetrievalResult(
        candidates=[RetrievalCandidate(runbook_id="rb-1", score=0.3)],
        confidence_threshold=_THRESHOLD,
    )


def _strong_match_retrieval() -> RetrievalResult:
    return RetrievalResult(
        candidates=[RetrievalCandidate(runbook_id="rb-2", score=0.9)],
        confidence_threshold=_THRESHOLD,
    )


def _boundary_retrieval(score: float) -> RetrievalResult:
    return RetrievalResult(
        candidates=[RetrievalCandidate(runbook_id="rb-3", score=score)],
        confidence_threshold=_THRESHOLD,
    )


# ── Tier 1: empty graph ────────────────────────────────────────────────────────


class TestTier1Inconclusive:
    """
    Tier 1 — no observable evidence.

    The orchestrator must return INCONCLUSIVE and must NEVER touch the agent.
    This is the structural zero-hallucination guarantee for the no-data case.
    """

    @pytest.fixture
    def agent(self) -> AgentPort:
        return _stub_agent()

    @pytest.fixture
    def result(self, agent: AgentPort):
        request = InvestigationRequest(
            component="api-gateway",
            graph=_empty_graph(),
            fsm_state=ProcessHealthStatus.HEALTHY,
            retrieval_result=RetrievalResult(),
        )
        return InvestigationOrchestrator(agent).run(request), agent

    def test_tier_is_inconclusive(self, result):
        diagnosis, _ = result
        assert diagnosis.tier == DiagnosisTier.INCONCLUSIVE

    def test_causal_chains_are_empty(self, result):
        diagnosis, _ = result
        assert diagnosis.causal_chains == []

    def test_requires_human_approval(self, result):
        diagnosis, _ = result
        assert diagnosis.remediation.requires_human_approval is True

    def test_evidence_references_empty(self, result):
        diagnosis, _ = result
        assert diagnosis.remediation.evidence_references == []

    def test_agent_was_never_called(self, result):
        """Critical: the LLM agent must not be invoked for a Tier-1 result."""
        _, agent = result
        agent.diagnose.assert_not_called()  # type: ignore[attr-defined]

    def test_fsm_state_is_preserved(self, result):
        diagnosis, _ = result
        assert diagnosis.fsm_state == ProcessHealthStatus.HEALTHY


# ── Tier 2: chain, no RAG match ────────────────────────────────────────────────


class TestTier2ChainOnly:
    """
    Tier 2 — causal chain exists, retriever returned no strong match.

    The agent must NOT be called.  The remediation must state that no known
    fix exists and evidence_references must be empty.  The causal chain itself
    must be propagated so the engineer can inspect it.
    """

    @pytest.fixture
    def agent(self) -> AgentPort:
        return _stub_agent()

    @pytest.fixture
    def result(self, agent: AgentPort):
        graph, _, _ = _two_node_causal_graph()
        request = InvestigationRequest(
            component="auth-service",
            graph=graph,
            fsm_state=ProcessHealthStatus.CRASHING,
            retrieval_result=_no_match_retrieval(),
        )
        return InvestigationOrchestrator(agent).run(request), agent

    def test_tier_is_chain_only(self, result):
        diagnosis, _ = result
        assert diagnosis.tier == DiagnosisTier.CHAIN_ONLY

    def test_causal_chains_are_populated(self, result):
        diagnosis, _ = result
        assert len(diagnosis.causal_chains) > 0

    def test_requires_human_approval(self, result):
        diagnosis, _ = result
        assert diagnosis.remediation.requires_human_approval is True

    def test_evidence_references_empty(self, result):
        """The agent was not called so there is no grounding; references must be []."""
        diagnosis, _ = result
        assert diagnosis.remediation.evidence_references == []

    def test_summary_mentions_no_known_fix(self, result):
        """The summary must communicate that no historical fix was found."""
        diagnosis, _ = result
        assert "no known" in diagnosis.remediation.summary.lower()

    def test_agent_was_never_called(self, result):
        """Critical: the LLM agent must not be invoked for a Tier-2 result."""
        _, agent = result
        agent.diagnose.assert_not_called()  # type: ignore[attr-defined]

    def test_fsm_state_is_preserved(self, result):
        diagnosis, _ = result
        assert diagnosis.fsm_state == ProcessHealthStatus.CRASHING


# ── Tier 3: chain + strong RAG match ──────────────────────────────────────────


class TestTier3Full:
    """
    Tier 3 — causal chain + at least one runbook above threshold.

    The agent IS called exactly once.  The result is FULL and still requires
    human approval.  Evidence references are populated from the strong candidates.
    """

    @pytest.fixture
    def agent(self) -> AgentPort:
        return _stub_agent()

    @pytest.fixture
    def result(self, agent: AgentPort):
        graph, _, _ = _two_node_causal_graph()
        request = InvestigationRequest(
            component="payment-service",
            graph=graph,
            fsm_state=ProcessHealthStatus.CRASH_LOOP,
            retrieval_result=_strong_match_retrieval(),
        )
        return InvestigationOrchestrator(agent).run(request), agent

    def test_tier_is_full(self, result):
        diagnosis, _ = result
        assert diagnosis.tier == DiagnosisTier.FULL

    def test_agent_was_called_exactly_once(self, result):
        _, agent = result
        agent.diagnose.assert_called_once()  # type: ignore[attr-defined]

    def test_requires_human_approval(self, result):
        """Tier-3 still requires human approval before any tool execution."""
        diagnosis, _ = result
        assert diagnosis.remediation.requires_human_approval is True

    def test_evidence_references_populated(self, result):
        diagnosis, _ = result
        assert "rb-2" in diagnosis.remediation.evidence_references

    def test_causal_chains_are_populated(self, result):
        diagnosis, _ = result
        assert len(diagnosis.causal_chains) > 0

    def test_summary_comes_from_agent(self, result):
        diagnosis, _ = result
        assert diagnosis.remediation.summary == "agent diagnosis summary"

    def test_fsm_state_is_preserved(self, result):
        diagnosis, _ = result
        assert diagnosis.fsm_state == ProcessHealthStatus.CRASH_LOOP


# ── Boundary: score == threshold ───────────────────────────────────────────────


class TestTierBoundaryAtThreshold:
    """
    Score exactly equal to the threshold must count as a strong match (>=)
    and therefore select Tier 3.
    """

    def test_score_at_threshold_selects_tier3(self):
        agent = _stub_agent()
        graph, _, _ = _two_node_causal_graph()
        request = InvestigationRequest(
            component="worker",
            graph=graph,
            fsm_state=ProcessHealthStatus.DEGRADED,
            retrieval_result=_boundary_retrieval(score=_THRESHOLD),  # exactly at boundary
        )
        result = InvestigationOrchestrator(agent).run(request)
        assert result.tier == DiagnosisTier.FULL
        agent.diagnose.assert_called_once()  # type: ignore[attr-defined]


# ── Boundary: score just below threshold ───────────────────────────────────────


class TestTierBoundaryBelowThreshold:
    """
    Score just below the threshold must NOT count as a strong match and
    therefore select Tier 2, with the agent never called.
    """

    def test_score_below_threshold_selects_tier2(self):
        agent = _stub_agent()
        graph, _, _ = _two_node_causal_graph()
        below = _THRESHOLD - 0.001
        request = InvestigationRequest(
            component="worker",
            graph=graph,
            fsm_state=ProcessHealthStatus.DEGRADED,
            retrieval_result=_boundary_retrieval(score=below),
        )
        result = InvestigationOrchestrator(agent).run(request)
        assert result.tier == DiagnosisTier.CHAIN_ONLY
        agent.diagnose.assert_not_called()  # type: ignore[attr-defined]

"""Unit tests for AgnoGroqAgent — no network: Agno agents are replaced by fakes.

Covers the deterministic guardrails around the LLM: construction checks, the
evidence validator (hallucinated runbook IDs are stripped), tool-discovered IDs,
fail-closed behaviour on provider errors, multi-turn session rules, the
search_runbooks tool and the prompt formatting helpers.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from agno.run.base import RunStatus
from deployd.adapters.outgoing.ai import agno_agent
from deployd.adapters.outgoing.ai.agno_agent import (
    MAX_FOLLOW_UP_TURNS,
    AgnoGroqAgent,
    _build_user_message,
    _format_candidates,
    _format_chains,
    _format_diagnosis,
    _make_check_dependencies_tool,
    _make_get_runbook_detail_tool,
    _make_search_runbooks_tool,
)
from deployd.adapters.outgoing.registry.json_component_repository import (
    JSONComponentRepository,
)
from deployd.adapters.outgoing.vector_store.chroma_client import DenseHit
from deployd.adapters.outgoing.vector_store.runbook_repository import JSONRunbookRepository
from deployd.application.dtos.diagnosis import AgentDiagnosis
from deployd.application.dtos.retrieval import RetrievalCandidate
from deployd.domain.entities.core_event import CoreEvent, CoreEventType, Severity
from deployd.domain.graph.node import GraphNode

OOM_ID = "RB-AUTH-SERVICE-OOMKILL"
DB_ID = "RB-PAYMENT-DB-TIMEOUT"


# ── Fakes ─────────────────────────────────────────────────────────────────────


class _FakeChroma:
    def __init__(self, hits: list[DenseHit]) -> None:
        self.hits = hits
        self.queries: list[str] = []

    def search(self, query: str, top_k: int, **_: Any) -> list[DenseHit]:
        self.queries.append(query)
        return self.hits[:top_k]


class _FakeAgnoAgent:
    """Stands in for agno.agent.Agent: returns a canned run response."""

    def __init__(self, respond: Callable[[str], Any]) -> None:
        self._respond = respond
        self.messages: list[str] = []

    def run(self, message: str) -> Any:
        self.messages.append(message)
        return self._respond(message)


def _response(content: Any, status: RunStatus = RunStatus.completed, tokens: int = 100) -> Any:
    return SimpleNamespace(
        content=content, status=status, metrics=SimpleNamespace(total_tokens=tokens)
    )


def _diagnosis(*evidence: str, confidence: str = "High") -> AgentDiagnosis:
    return AgentDiagnosis(
        root_cause="Session cache growth exhausted auth-service heap.",
        confidence=confidence,
        reasoning="Config reload → memory 97% → timeouts.",
        recommendation="Roll back the session cache size change.",
        evidence_references=list(evidence),
    )


def _node(event_type: CoreEventType, second: int, component: str = "auth-service") -> GraphNode:
    return GraphNode(
        event=CoreEvent(
            event_type=event_type,
            severity=Severity.ERROR,
            timestamp=datetime(2026, 9, 30, 10, 0, second, tzinfo=timezone.utc),
            related_component=component,
            description=f"{event_type.value} on {component}",
        )
    )


CHAIN = [
    _node(CoreEventType.STATE_CHANGE, 0),
    _node(CoreEventType.RESOURCE_EXHAUSTION, 30),
    _node(CoreEventType.DEPENDENCY_FAILURE, 45, "api-gateway"),
]


@pytest.fixture
def groq_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GROQ_API_KEY", "test-key-not-used")


@pytest.fixture
def repo() -> JSONRunbookRepository:
    return JSONRunbookRepository("data/runbooks")


def _agent_with(
    monkeypatch: pytest.MonkeyPatch, respond: Callable[[str], Any], **deps: Any
) -> tuple[AgnoGroqAgent, _FakeAgnoAgent]:
    agent = AgnoGroqAgent(**deps)
    fake = _FakeAgnoAgent(respond)
    monkeypatch.setattr(agent, "_create_structured_agent", lambda: fake)
    return agent, fake


# ── Construction ─────────────────────────────────────────────────────────────


def test_missing_api_key_fails_fast(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="GROQ_API_KEY"):
        AgnoGroqAgent()


@pytest.mark.usefixtures("groq_key")
def test_tools_are_registered_only_for_injected_dependencies(
    repo: JSONRunbookRepository,
) -> None:
    assert AgnoGroqAgent()._tools == []
    with_repo = AgnoGroqAgent(chroma_client=_FakeChroma([]), runbook_repo=repo)  # type: ignore[arg-type]  # fake
    assert [t.__name__ for t in with_repo._tools] == ["search_runbooks", "get_runbook_detail"]


@pytest.mark.usefixtures("groq_key")
def test_structured_agent_uses_parser_model_only_with_tools(repo: JSONRunbookRepository) -> None:
    # Groq rejects JSON mode + tool calling; with tools a separate parser pass is required.
    plain = AgnoGroqAgent()._create_structured_agent()
    tooled = AgnoGroqAgent(
        chroma_client=_FakeChroma([]),  # type: ignore[arg-type]  # fake
        runbook_repo=repo,
    )._create_structured_agent()

    assert plain.parser_model is None
    assert tooled.parser_model is not None
    assert tooled.output_schema is AgentDiagnosis


# ── diagnose(): evidence validator and fail-closed behaviour ──────────────────


@pytest.mark.usefixtures("groq_key")
def test_diagnose_strips_hallucinated_runbook_ids(monkeypatch: pytest.MonkeyPatch) -> None:
    agent, fake = _agent_with(
        monkeypatch, lambda _: _response(_diagnosis(OOM_ID, "RB-INVENTED-BY-MODEL"), tokens=812)
    )

    result = agent.diagnose("auth-service", [CHAIN], [RetrievalCandidate(OOM_ID, 0.67)])

    assert result.evidence_references == [OOM_ID]
    assert result.confidence == "High"
    assert agent.last_token_usage == 812
    assert agent.last_session_id is not None
    # The prompt carries the component, the chain and the retrieved candidate.
    sent = fake.messages[0]
    assert "auth-service" in sent and OOM_ID in sent and "RESOURCE_EXHAUSTION" in sent


@pytest.mark.usefixtures("groq_key")
def test_validator_rejects_valid_runbooks_not_retrieved_for_this_incident(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # DB_ID exists in the corpus but was not part of this investigation's evidence.
    agent, _ = _agent_with(monkeypatch, lambda _: _response(_diagnosis(OOM_ID, DB_ID)))

    result = agent.diagnose("auth-service", [CHAIN], [RetrievalCandidate(OOM_ID, 0.67)])

    assert result.evidence_references == [OOM_ID]


@pytest.mark.usefixtures("groq_key")
def test_ids_discovered_by_the_search_tool_are_accepted(
    monkeypatch: pytest.MonkeyPatch, repo: JSONRunbookRepository
) -> None:
    chroma = _FakeChroma([DenseHit(DB_ID, 0.81)])

    def respond(_: str) -> Any:
        agent._tools[0]("payment database connection timeouts")  # model calls search_runbooks
        return _response(_diagnosis(OOM_ID, DB_ID))

    agent, _ = _agent_with(monkeypatch, respond, chroma_client=chroma, runbook_repo=repo)

    result = agent.diagnose("auth-service", [CHAIN], [RetrievalCandidate(OOM_ID, 0.67)])

    assert result.evidence_references == [OOM_ID, DB_ID]


@pytest.mark.usefixtures("groq_key")
def test_tool_discovered_ids_do_not_leak_into_the_next_investigation(
    monkeypatch: pytest.MonkeyPatch, repo: JSONRunbookRepository
) -> None:
    chroma = _FakeChroma([DenseHit(DB_ID, 0.81)])
    calls = {"n": 0}

    def respond(_: str) -> Any:
        calls["n"] += 1
        if calls["n"] == 1:
            agent._tools[0]("payment database timeouts")
        return _response(_diagnosis(DB_ID))

    agent, _ = _agent_with(monkeypatch, respond, chroma_client=chroma, runbook_repo=repo)
    agent.diagnose("payment-service", [CHAIN], [])
    second = agent.diagnose("auth-service", [CHAIN], [RetrievalCandidate(OOM_ID, 0.67)])

    assert second.evidence_references == []


@pytest.mark.usefixtures("groq_key")
def test_provider_error_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    # Agno reports rate limits as *content* with status ERROR — never treat it as an answer.
    agent, _ = _agent_with(
        monkeypatch,
        lambda _: _response('{"error": "rate_limit_exceeded"}', status=RunStatus.error),
    )

    with pytest.raises(RuntimeError, match="rate_limit_exceeded"):
        agent.diagnose("auth-service", [CHAIN], [RetrievalCandidate(OOM_ID, 0.67)])
    assert agent.last_session_id is None


@pytest.mark.usefixtures("groq_key")
def test_unstructured_output_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    agent, _ = _agent_with(monkeypatch, lambda _: _response("free text, not a schema"))

    with pytest.raises(TypeError, match="Expected AgentDiagnosis"):
        agent.diagnose("auth-service", [CHAIN], [])


# ── follow_up(): multi-turn session rules ─────────────────────────────────────


def _diagnosed_agent(monkeypatch: pytest.MonkeyPatch) -> tuple[AgnoGroqAgent, str]:
    agent, _ = _agent_with(monkeypatch, lambda _: _response(_diagnosis(OOM_ID)))
    agent.diagnose("auth-service", [CHAIN], [RetrievalCandidate(OOM_ID, 0.67)])
    assert agent.last_session_id is not None
    return agent, agent.last_session_id


@pytest.mark.usefixtures("groq_key")
def test_follow_up_frames_engineer_input_as_unverified(monkeypatch: pytest.MonkeyPatch) -> None:
    agent, session = _diagnosed_agent(monkeypatch)
    followup = _FakeAgnoAgent(
        lambda _: _response("Check the session cache size first.", tokens=321)
    )
    monkeypatch.setattr(agent, "_create_followup_agent", lambda ctx: followup)

    answer = agent.follow_up(session, "  We rolled back already.  ")

    assert answer.root_cause == "Check the session cache size first."
    assert agent.last_token_usage == 321
    assert "UNVERIFIED" in followup.messages[0]
    assert "Engineer: We rolled back already." in followup.messages[0]
    ctx = agent._sessions[session]
    assert ctx.turn_count == 1
    assert ctx.followup_history == [("We rolled back already.", answer.root_cause)]


@pytest.mark.usefixtures("groq_key")
def test_follow_up_unknown_session_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    agent, _ = _diagnosed_agent(monkeypatch)
    with pytest.raises(ValueError, match="Unknown session"):
        agent.follow_up("does-not-exist", "hello")


@pytest.mark.usefixtures("groq_key")
def test_empty_follow_up_never_calls_the_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    agent, session = _diagnosed_agent(monkeypatch)
    monkeypatch.setattr(agent, "_create_followup_agent", _fail_if_called)

    answer = agent.follow_up(session, "   ")

    assert answer.confidence == "Low"
    assert agent._sessions[session].turn_count == 0


@pytest.mark.usefixtures("groq_key")
def test_turn_limit_stops_the_session(monkeypatch: pytest.MonkeyPatch) -> None:
    agent, session = _diagnosed_agent(monkeypatch)
    agent._sessions[session].turn_count = MAX_FOLLOW_UP_TURNS
    monkeypatch.setattr(agent, "_create_followup_agent", _fail_if_called)

    answer = agent.follow_up(session, "one more question")

    assert "Maximum follow-up turns" in answer.root_cause
    assert agent._sessions[session].turn_count == MAX_FOLLOW_UP_TURNS


@pytest.mark.usefixtures("groq_key")
def test_follow_up_provider_error_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    agent, session = _diagnosed_agent(monkeypatch)
    failing = _FakeAgnoAgent(lambda _: _response("rate_limit_exceeded", status=RunStatus.error))
    monkeypatch.setattr(agent, "_create_followup_agent", lambda ctx: failing)

    with pytest.raises(RuntimeError, match="Follow-up failed"):
        agent.follow_up(session, "what should I check first?")


def _fail_if_called(*_: Any) -> Any:
    raise AssertionError("the LLM must not be called")


# ── search_runbooks tool ──────────────────────────────────────────────────────


def test_search_tool_rejects_too_short_queries(repo: JSONRunbookRepository) -> None:
    chroma = _FakeChroma([DenseHit(OOM_ID, 0.9)])
    tool = _make_search_runbooks_tool(chroma, repo, set())  # type: ignore[arg-type]  # fake

    assert tool("  a ").startswith("Error")
    assert chroma.queries == []


def test_search_tool_registers_returned_ids_and_caps_output(repo: JSONRunbookRepository) -> None:
    seen: set[str] = set()
    chroma = _FakeChroma([DenseHit(OOM_ID, 0.9), DenseHit(DB_ID, 0.5)])
    tool = _make_search_runbooks_tool(chroma, repo, seen)  # type: ignore[arg-type]  # fake

    output = tool("auth service " + "x" * 2000)

    assert seen == {OOM_ID, DB_ID}
    assert OOM_ID in output
    assert len(output) <= agno_agent._TOOL_OUTPUT_MAX_CHARS
    assert len(chroma.queries[0]) == agno_agent._TOOL_QUERY_MAX_CHARS


def test_search_tool_handles_no_hits(repo: JSONRunbookRepository) -> None:
    tool = _make_search_runbooks_tool(_FakeChroma([]), repo, set())  # type: ignore[arg-type]  # fake
    assert tool("disk full on inventory") == "No matching runbooks found for this query."


# ── get_runbook_detail tool ───────────────────────────────────────────────────


@pytest.mark.parametrize("runbook_id", [OOM_ID, " rb-auth-service-oomkill "])
def test_detail_tool_returns_real_runbooks(repo: JSONRunbookRepository, runbook_id: str) -> None:
    output = _make_get_runbook_detail_tool(repo)(runbook_id)

    assert output.startswith(f"Runbook: {OOM_ID}")
    assert "Commands:" in output
    assert len(output) <= agno_agent._TOOL_OUTPUT_MAX_CHARS


@pytest.mark.parametrize("runbook_id", ["rb_auth_service_oom", "", "RB-", "RB-X; DROP TABLE"])
def test_detail_tool_rejects_malformed_ids(repo: JSONRunbookRepository, runbook_id: str) -> None:
    assert _make_get_runbook_detail_tool(repo)(runbook_id).startswith("Error: invalid runbook_id")


def test_detail_tool_reports_unknown_ids(repo: JSONRunbookRepository) -> None:
    output = _make_get_runbook_detail_tool(repo)("RB-DOES-NOT-EXIST")

    assert output == "Runbook 'RB-DOES-NOT-EXIST' not found in the historical database."


# ── check_component_dependencies tool ─────────────────────────────────────────


@pytest.fixture
def registry() -> JSONComponentRepository:
    return JSONComponentRepository(
        Path("data/components.json"), Path("data/compatibility_constraints.json")
    )


def test_dependency_tool_reports_registered_component(registry: JSONComponentRepository) -> None:
    evidence: set[str] = set()

    output = _make_check_dependencies_tool(registry, evidence)(" Payment-Service ")

    assert "Status: INCOMPATIBLE" in output
    assert "pydantic: 1.10.14" in output
    assert len(evidence) == 1 and next(iter(evidence)) in output


def test_dependency_tool_gives_no_evidence_for_unknown_components(
    registry: JSONComponentRepository,
) -> None:
    evidence: set[str] = set()

    output = _make_check_dependencies_tool(registry, evidence)("does-not-exist")

    assert output == "Component 'does-not-exist' is not in the registry; no compatibility evidence."
    assert evidence == set()


@pytest.mark.parametrize("component", ["", "payment service", "../etc/passwd", "x" * 65])
def test_dependency_tool_rejects_malformed_names(
    registry: JSONComponentRepository, component: str
) -> None:
    evidence: set[str] = set()

    output = _make_check_dependencies_tool(registry, evidence)(component)

    assert output.startswith("Error: invalid component name")
    assert evidence == set()


# ── Prompt formatting helpers ─────────────────────────────────────────────────


def test_format_chains_includes_components_and_time_deltas() -> None:
    text = _format_chains([CHAIN])

    assert "Chain 1 (3 events)" in text
    assert "component=api-gateway" in text
    assert "Δt=30.0s" in text and "Δt=15.0s" in text
    assert _format_chains([]) == "No causal chains identified."


def test_format_candidates_labels_hybrid_scores() -> None:
    assert _format_candidates([RetrievalCandidate(OOM_ID, 0.672)]) == (
        f"- {OOM_ID} (hybrid_score: 0.67)"
    )
    assert _format_candidates([]) == "No historical runbooks retrieved."


def test_build_user_message_marks_system_evidence_as_verified() -> None:
    message = _build_user_message("auth-service", [CHAIN], [RetrievalCandidate(OOM_ID, 0.67)])

    assert "component 'auth-service'" in message
    assert "## System Evidence (verified)" in message
    assert OOM_ID in message


def test_format_diagnosis_without_evidence() -> None:
    text = _format_diagnosis(_diagnosis())

    assert "**Evidence**: None" in text
    assert "**Confidence**: High" in text

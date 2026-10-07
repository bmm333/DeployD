"""DID-17: AgnoGroqAgent — Multi-turn investigative agent with tool calling.

Architecture
------------
The agent is an *outgoing adapter* in DeployD architecture.
It implements the ``AgentPort`` protocol defined by the
``InvestigationOrchestrator``, receiving pre-computed evidence (causal chains
+ retrieval candidates) and producing a **validated** diagnosis.

The diagnosis pipeline is::
    LLM (with tools)-> AgentDiagnosis (Pydantic schema)->Evidence validator (deterministic)->Formatted string-> DiagnosisResult

Tools are **read-only** and every input/output is validated

Multi-turn support distinguishes two evidence categories:
  - **System evidence** — verified data from IncidentGraph, FSM, retrieval.
  - **Engineer-provided context** — unverified hypotheses from follow-up
    messages, explicitly framed as such in the agent prompt.

Model choice
------------
Groq / openai/gpt-oss-120b — fast inference matters when an engineer is
waiting for a diagnosis during an active incident, and it supports tool calling
plus structured output.  Llama-3.3-70b-versatile is no longer served by Groq;
qwen/qwen3.8-27b is capped at 1000 output tokens/min on the free tier, which a
single grounded diagnosis already exceeds.  The free tier (8000 tokens/min) is
sufficient for the project demo: ~3k tokens per diagnosis, ~2k per follow-up.
"""

from __future__ import annotations

import html
import logging
import os
import re
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from agno.agent import Agent
from agno.models.groq import Groq
from agno.run.base import RunStatus
from deployd.adapters.outgoing.ai.tool_models import (
    DependencyCheckInput,
    DependencyCheckResult,
    RunbookDetail,
    RunbookDetailInput,
    RunbookSearchInput,
    RunbookSearchResult,
    RunbookSummary,
    ToolError,
)
from deployd.application.dtos.diagnosis import AgentDiagnosis
from pydantic import BaseModel, Field, ValidationError

if TYPE_CHECKING:
    from collections.abc import Callable

    from deployd.adapters.outgoing.vector_store.chroma_client import (
        ChromaRunbookClient,
    )
    from deployd.adapters.outgoing.vector_store.runbook_repository import (
        JSONRunbookRepository,
    )
    from deployd.application.dtos.retrieval import RetrievalCandidate
    from deployd.domain.components.component_registry import ComponentRegistry
    from deployd.domain.graph.node import GraphNode

logger = logging.getLogger(__name__)


@dataclass
class _SessionContext:
    """Internal state for one multi-turn investigation session."""

    component: str
    initial_evidence: str  # formatted user message
    diagnosis_text: str  # formatted diagnosis
    allowed_evidence_ids: set[str] = field(default_factory=set)  # IDs agent may cite
    followup_agent: Agent | None = None
    turn_count: int = 0
    followup_history: list[tuple[str, str]] = field(default_factory=list)


class _FollowUpAnswer(BaseModel):
    """Structured output of a follow-up turn, mapped onto ``AgentDiagnosis`` for the port."""

    answer: str = Field(
        description="Direct answer to the engineer, about the investigated component only"
    )
    confidence: Literal["High", "Medium", "Low"] = Field(
        description="Certainty of this answer given the system evidence"
    )
    evidence_references: list[str] = Field(
        default_factory=list,
        description="Runbook or compatibility evidence IDs this answer relies on; empty if none",
    )


# Prompt Loading cached

_PROMPT_VERSION_HEADER = re.compile(r"# prompt-version: (\d+\.\d+\.\d+)\n")
_PROMPT_CACHE: tuple[str, str] | None = None


def _load_system_prompt() -> tuple[str, str]:
    """Load and cache ``(version, text)`` of ``prompts/agno_diagnosis.txt``.

    The file must start with ``# prompt-version: X.Y.Z``; the header is stripped
    from the text sent to the model.
    """
    global _PROMPT_CACHE  # noqa: PLW0603
    if _PROMPT_CACHE is not None:
        return _PROMPT_CACHE

    prompt_path = _repo_root() / "prompts" / "agno_diagnosis.txt"
    if not prompt_path.exists():
        raise FileNotFoundError(
            f"System prompt not found at {prompt_path}. "
            "Create prompts/agno_diagnosis.txt before initializing the agent."
        )
    _PROMPT_CACHE = _parse_prompt(prompt_path.read_text(encoding="utf-8"))
    return _PROMPT_CACHE


def _parse_prompt(raw: str) -> tuple[str, str]:
    header = _PROMPT_VERSION_HEADER.match(raw)
    if header is None:
        raise ValueError("System prompt must start with '# prompt-version: X.Y.Z'")
    return header.group(1), raw[header.end() :]


def _repo_root() -> Path:
    """Walk up from this file to find the repo root (contains pyproject.toml)."""
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "pyproject.toml").exists():
            return parent
    raise RuntimeError("Could not locate repo root (no pyproject.toml in ancestors)")


# Tools validate their arguments with Pydantic, answer with a Pydantic result
# serialised as JSON, and never return more than _TOOL_OUTPUT_MAX_CHARS.

_TOOL_OUTPUT_MAX_CHARS = 1500


def _untrusted(text: str) -> str:
    """Escape untrusted text so it cannot open or close the prompt's delimiter tags."""
    return html.escape(text, quote=False)


def _tool_error(message: str) -> str:
    return ToolError(error=message).model_dump_json()


def _invalid_args(exc: ValidationError) -> str:
    first = exc.errors()[0]
    field_name = ".".join(str(part) for part in first["loc"])
    return _tool_error(f"invalid {field_name}: {first['msg']}")


_TOO_LARGE = _tool_error("result exceeds the tool output limit")


def _capped(result: BaseModel) -> str | None:
    """The result as JSON, or ``None`` when over the limit (JSON cannot be cut without breaking)."""
    output = result.model_dump_json()
    return output if len(output) <= _TOOL_OUTPUT_MAX_CHARS else None


def _make_search_runbooks_tool(
    chroma: ChromaRunbookClient,
    runbook_repo: JSONRunbookRepository,
    retrieved_ids: set[str],
) -> Callable[..., str]:
    """Create a ``search_runbooks`` tool bound to *chroma* and *runbook_repo*.

    Note: this tool uses **semantic-only** search via ChromaDB, not the full
    HybridRetriever pipeline (BM25 + Dense + Causal matching).  This is by
    design. The agent's tool is for  additional 5lightweight searches during investigation.

    The *retrieved_ids* set is mutated in-place: every runbook ID returned
    by a tool call is added so that ``_validate_evidence`` can accept it.
    """

    def search_runbooks(query: str) -> str:
        """Search historical runbooks for incidents matching the query.

        Used when need past incidents similar to the current one,
        or when the engineer mentions things not covered by the evidence

        Args:
            query: Natural language description of the incident or symptoms.

        Returns:
            JSON {"runbooks": [...]} with summary, root cause, causal chain and
            fix of each match, or {"error": ...}.
        """
        try:
            args = RunbookSearchInput(query=query)
        except ValidationError as exc:
            return _invalid_args(exc)

        logger.info("Tool call: search_runbooks(query=%r)", args.query[:80])

        result = RunbookSearchResult(runbooks=[])
        output = result.model_dump_json()
        for hit in chroma.search(args.query, top_k=3):
            detail = runbook_repo.get_by_id(hit.runbook_id)
            if not detail:
                continue
            match = RunbookSummary(
                runbook_id=detail.runbook_id,
                semantic_score=round(float(hit.semantic_score), 2),
                summary=detail.summary[:200],
                root_cause=detail.root_cause[:150],
                causal_chain=list(detail.causal_chain),
                fix=detail.fix[:150],
                affected_components=list(detail.affected_components),
            )
            extended = RunbookSearchResult(runbooks=[*result.runbooks, match])
            extended_output = _capped(extended)
            if extended_output is None:
                break
            # Only IDs actually shown to the model become citable evidence.
            retrieved_ids.add(detail.runbook_id)
            result, output = extended, extended_output

        return output

    return search_runbooks


def _make_check_dependencies_tool(
    registry: ComponentRegistry,
    retrieved_ids: set[str],
) -> Callable[..., str]:
    """Create a ``check_component_dependencies`` tool bound to *registry*."""

    def check_component_dependencies(component: str) -> str:
        """Check the software state and compatibility constraints of a component.

        Use this when need to verify if a component has dependency conflicts,
        version mismatches, or missing requirements that could explain a crash.
        This tool performs a deterministic check against known constraints.

        Args:
            component: The name of the component (e.g., 'payment-service').

        Returns:
            JSON compatibility report (evidence_id, status, installed_versions,
            violations), or {"error": ...}.
        """
        try:
            args = DependencyCheckInput(component=component)
        except ValidationError as exc:
            return _invalid_args(exc)

        logger.info("Tool call: check_component_dependencies(component=%r)", args.component)

        if registry.get_component_state(args.component) is None:
            return _tool_error(
                f"component '{args.component}' is not in the registry; no compatibility evidence"
            )

        report = registry.check_compatibility(args.component)
        output = _capped(
            DependencyCheckResult.model_validate(
                {
                    "evidence_id": report.evidence_id,
                    "component": report.component_name,
                    "status": report.status.value.upper(),
                    "installed_versions": report.installed_versions,
                    "violations": report.violations,
                }
            )
        )
        if output is None:
            return _TOO_LARGE
        # Register the evidence ID only once the model can actually see it.
        retrieved_ids.add(report.evidence_id)
        return output

    return check_component_dependencies


def _make_get_runbook_detail_tool(
    runbook_repo: JSONRunbookRepository,
) -> Callable[..., str]:
    """Create a ``get_runbook_detail`` tool bound to *runbook_repo*.

    This is the "detailed(or expensie)" half of the progressive retrieval
    pattern:
        search_runbooks->cheap, broad candidate; discovery get_runbook_detail->specific, full evidence for a known ID
    """

    def get_runbook_detail(runbook_id: str) -> str:
        """Get the full details and fix commands of a specific runbook.

        Use this when already know a runbook ID from the initial evidence
        and need the complete remediation procedure, including commands.

        Args:
            runbook_id: The runbook identifier (e.g. 'RB-PAYMENT-DB-TIMEOUT').

        Returns:
            JSON runbook with root cause, causal chain, fix and fix_commands,
            or {"error": ...}.
        """
        try:
            args = RunbookDetailInput(runbook_id=runbook_id)
        except ValidationError as exc:
            return _invalid_args(exc)

        logger.info("Tool call: get_runbook_detail(runbook_id=%r)", args.runbook_id)

        detail = runbook_repo.get_by_id(args.runbook_id)
        if not detail:
            return _tool_error(f"runbook '{args.runbook_id}' not found in the historical database")

        result = RunbookDetail(
            runbook_id=detail.runbook_id,
            incident_id=detail.incident_id,
            summary=detail.summary[:300],
            root_cause=detail.root_cause[:300],
            causal_chain=list(detail.causal_chain),
            fix=detail.fix[:300],
            fix_commands=[command[:200] for command in detail.fix_commands[:5]],
            affected_components=list(detail.affected_components),
        )
        return _capped(result) or _TOO_LARGE

    return get_runbook_detail


# Agent

MAX_FOLLOW_UP_TURNS = 5
_MAX_FOLLOW_UP_TURNS = MAX_FOLLOW_UP_TURNS

# Run limits enforced by Agno/Groq rather than requested in the prompt: a run
# can never loop on tools, and a low temperature keeps diagnoses reproducible.
# max_tokens covers gpt-oss reasoning plus the answer (~1k in practice).
TOOL_CALL_LIMIT = 4
TEMPERATURE = 0.1
MAX_OUTPUT_TOKENS = 2048


class AgnoGroqAgent:
    """Multi-turn investigative agent for AIOps incident diagnosis.

    Implements the ``AgentPort`` protocol (investigation_orchestrator.py).
    The orchestrator calls ``diagnose()`` in Tier 3 with pre-computed
    evidence.  The agent synthesises the evidence into a **validated**
    structured diagnosis, optionally using tools for deeper investigation,
    and supports multi-turn follow-up with engineers.

    Diagnosis pipeline::

        LLM (with tools)->AgentDiagnosis (Pydantic response_model)->Evidence validator (deterministic — strips hallucinated IDs) -> Formatted string for AgentPort

    Tool safety boundary: every tool is **read-only**.  ``search_runbooks``,
    ``get_runbook_detail`` and ``check_component_dependencies`` only read the
    runbook store and the component registry; none writes state, calls an
    external system or executes a command.  Arguments and results are Pydantic
    models (``tool_models.py``), results are capped JSON, and Agno refuses tool
    calls beyond ``TOOL_CALL_LIMIT`` per run.

    Parameters
    ----------
    chroma_client: Optional.  ChromaDB client for semantic runbook search (tool dep).
    runbook_repo: Optional.  JSON runbook repository for full runbook data (tool dep + evidence validation).
    When tool dependencies are **not** provided the agent operates in
    summarisation-only mode (no tool calling, no evidence validation).
    This preserves backward compatibility with the current orchestrator.
    """

    MODEL_ID = "openai/gpt-oss-120b"

    def __init__(
        self,
        *,
        chroma_client: ChromaRunbookClient | None = None,
        runbook_repo: JSONRunbookRepository | None = None,
        component_registry: ComponentRegistry | None = None,
    ) -> None:
        # Fail-fast: validate API key at construction time
        if not os.environ.get("GROQ_API_KEY"):
            raise RuntimeError(
                "GROQ_API_KEY environment variable is required. "
                "Get a free key at https://console.groq.com"
            )

        # Load prompt template once
        self._prompt_version, self._system_prompt = _load_system_prompt()

        # Store repo for evidence validation
        self._runbook_repo = runbook_repo

        # Build tool list from injected dependencies
        self._tools: list[Callable[..., str]] = []
        # Track IDs discovered by tools during a diagnose() call, so the
        # evidence validator can accept them alongside the orchestrator's
        # candidates.  Cleared at the start of each diagnose().
        self._tool_retrieved_ids: set[str] = set()
        if chroma_client is not None and runbook_repo is not None:
            self._tools.append(
                _make_search_runbooks_tool(chroma_client, runbook_repo, self._tool_retrieved_ids)
            )
        if runbook_repo is not None:
            self._tools.append(_make_get_runbook_detail_tool(runbook_repo))

        if component_registry is not None:
            self._tools.append(
                _make_check_dependencies_tool(component_registry, self._tool_retrieved_ids)
            )

        # Multi-turn session storag
        # In-memory dict for the PoC.  Production: use Redis / DB.
        self._sessions: dict[str, _SessionContext] = {}
        self._last_session_id: str | None = None
        self._last_token_usage: int | None = None

        logger.info(
            "AgnoGroqAgent initialised: model=%s, tools=%d",
            self.MODEL_ID,
            len(self._tools),
        )

    # AgentPort protocol

    def diagnose(
        self,
        component: str,
        causal_chains: list[list[GraphNode]],
        candidates: list[RetrievalCandidate],
    ) -> AgentDiagnosis:
        """Produce a validated, grounded diagnosis from pre-computed evidence.

        Pipeline:  LLM -> AgentDiagnosis -> evidence validator -> AgentDiagnosis.

        **Fail-closed**: if structured output or validation fails, the error
        propagates to the orchestrator which can degrade to Tier 2.  We do
        NOT fall back to an unstructured LLM call, because an unvalidated
        diagnosis in an incident response system is worse than no diagnosis.

        The ``last_session_id`` property exposes the session identifier for
        subsequent ``follow_up`` calls.
        """
        session_id = uuid.uuid4().hex[:12]
        user_message = _build_user_message(component, causal_chains, candidates)

        # Build the set of IDs the agent is allowed to cite: only those
        # that were actually retrieved by the orchestrator's pipeline.
        allowed_ids = {c.runbook_id for c in candidates}

        # Reset tool-discovered IDs for this investigation session.
        self._tool_retrieved_ids.clear()

        logger.info(
            "Diagnosis started: component=%s, chains=%d, candidates=%d, session=%s",
            component,
            len(causal_chains),
            len(candidates),
            session_id,
        )

        # Step 1: Run agent with structured output
        agent = self._create_structured_agent()
        response = agent.run(user_message)
        self._last_token_usage = _total_tokens(response)
        _raise_on_failed_run(response)

        # Agno returns content as Any when output_schema is set;
        # at runtime it will be an AgentDiagnosis instance.
        if not isinstance(response.content, AgentDiagnosis):
            msg = f"Expected AgentDiagnosis, got {type(response.content).__name__}"
            raise TypeError(msg)
        # Step 2: Evidence validation (deterministic)
        allowed_ids |= self._tool_retrieved_ids
        diagnosis = _validate_evidence(response.content, allowed_ids)

        # Store session context for follow-ups
        formatted = _format_diagnosis(diagnosis)
        self._sessions[session_id] = _SessionContext(
            component=component,
            initial_evidence=user_message,
            diagnosis_text=formatted,
            allowed_evidence_ids=allowed_ids,
        )
        self._last_session_id = session_id

        logger.info(
            "Diagnosis complete: session=%s, root_cause=%s",
            session_id,
            diagnosis.root_cause[:80],
        )
        return diagnosis

    # Multi-turn follow-up
    def follow_up(self, session_id: str, message: str) -> AgentDiagnosis:
        """Continue an investigation with additional context from the engineer.

        Engineer-provided context is explicitly framed as **unverified** in
        the agent prompt.  The agent retains the initial diagnosis and all
        prior follow-up turns as conversation context.

        The model answers with a structured ``_FollowUpAnswer`` (answer,
        confidence, cited evidence), wrapped in an ``AgentDiagnosis`` for
        Protocol consistency: ``root_cause`` carries the answer.  Its citations
        go through the same validator as the diagnosis, against this session's
        evidence (retrieved candidates plus IDs shown by tools in this session).

        **Scope constraint**: the underlying prompt restricts discussion to
        the component named in the original ``diagnose()`` call — the
        component-scope guardrail in the system prompt enforces this.

        Parameters
        ----------
        session_id:
            Identifier from ``last_session_id`` after calling ``diagnose``.
        message:
            Follow-up from the engineer (e.g. "we deployed v2.3 yesterday").

        Returns
        -------
        AgentDiagnosis wrapping the updated analysis.

        Raises
        ------
        ValueError:
            If *session_id* does not match any active session.
        """
        ctx = self._sessions.get(session_id)
        if ctx is None:
            raise ValueError(
                f"Unknown session '{session_id}'.  "
                "The session may have expired or was never created."
            )

        message = message.strip()
        if not message:
            return AgentDiagnosis(
                root_cause="No additional context was provided.",
                confidence="Low",
                reasoning="Empty follow-up message received.",
                recommendation="Please provide additional context or a specific question.",
                evidence_references=[],
            )

        if ctx.turn_count >= _MAX_FOLLOW_UP_TURNS:
            return AgentDiagnosis(
                root_cause=(
                    f"Maximum follow-up turns ({_MAX_FOLLOW_UP_TURNS}) reached. "
                    "Please start a new investigation."
                ),
                confidence="Low",
                reasoning=f"Turn limit of {_MAX_FOLLOW_UP_TURNS} exhausted for session {session_id}.",
                recommendation="Start a new investigation via diagnose().",
                evidence_references=[],
            )

        ctx.turn_count += 1

        logger.info(
            "Follow-up: session=%s, turn=%d, message_length=%d",
            session_id,
            ctx.turn_count,
            len(message),
        )

        # Engineer input is framed as unverified hypothesis
        framed_message = (
            "## Engineer-Provided Context (UNVERIFIED)\n"
            "The following was provided by the on-call engineer.  It has "
            "NOT been verified against the IncidentGraph or FSM.  Treat "
            "it as a hypothesis to investigate, not as confirmed evidence.\n\n"
            f"<engineer_input>\n{_untrusted(message)}\n</engineer_input>"
        )

        # Lazily create follow-up agent with full context
        if ctx.followup_agent is None:
            ctx.followup_agent = self._create_followup_agent(ctx)

        self._tool_retrieved_ids.clear()
        response = ctx.followup_agent.run(framed_message)
        self._last_token_usage = _total_tokens(response)
        _raise_on_failed_run(response)
        if not isinstance(response.content, _FollowUpAnswer):
            msg = f"Expected a structured follow-up answer, got {type(response.content).__name__}"
            raise TypeError(msg)
        reply = response.content

        ctx.allowed_evidence_ids |= self._tool_retrieved_ids
        answer = _validate_evidence(
            AgentDiagnosis(
                root_cause=reply.answer,
                confidence=reply.confidence,
                reasoning=f"Follow-up turn {ctx.turn_count} for component '{ctx.component}'.",
                recommendation="Review the answer and confirm next steps with the team.",
                evidence_references=reply.evidence_references,
            ),
            ctx.allowed_evidence_ids,
        )
        # Later turns see the validated answer, never the raw model text.
        ctx.followup_history.append((message, answer.root_cause))
        logger.info("Follow-up complete: session=%s, turn=%d", session_id, ctx.turn_count)
        return answer

    # Properties
    @property
    def last_session_id(self) -> str | None:
        """Session ID of the most recent ``diagnose()`` call.
        Use this to pass to ``follow_up()`` for multi-turn conversations.
        """
        return self._last_session_id

    @property
    def prompt_version(self) -> str:
        """Version of the system prompt this agent runs with."""
        return self._prompt_version

    @property
    def last_token_usage(self) -> int | None:
        """Total tokens (input + output) of the most recent LLM run, if reported."""
        return self._last_token_usage

    # Internal
    def _create_structured_agent(self) -> Agent:
        return self._create_agent(self._system_prompt, AgentDiagnosis)

    def _create_agent(self, instructions: str, output_schema: type[BaseModel]) -> Agent:
        """Agent with ``output_schema`` for validated structured output.

        Groq rejects JSON mode combined with tool calling.  With tools, the
        reasoning run therefore goes without a response format and a separate
        parser pass (same model, no tools) maps its answer onto the schema.
        """
        return Agent(
            model=self._model(),
            tools=self._tools or None,
            tool_call_limit=TOOL_CALL_LIMIT,
            instructions=instructions,
            output_schema=output_schema,
            parser_model=self._model() if self._tools else None,
            structured_outputs=True,
            markdown=False,
        )

    def _create_followup_agent(self, ctx: _SessionContext) -> Agent:
        """Agent for follow-up turns, with full investigation context injected.

        The agent receives the initial evidence and diagnosis as part of its
        instructions so it can reason about the full investigation history.
        """
        context_block = (
            f"\n\n## Previous Investigation Context\n"
            f"Component under investigation: {ctx.component}\n\n"
            f"### System Evidence (verified)\n{ctx.initial_evidence}\n\n"
            f"### Your Previous Diagnosis\n{ctx.diagnosis_text}"
        )

        # Include prior follow-up turns if any
        if ctx.followup_history:
            turns = []
            for eng_msg, agent_resp in ctx.followup_history:
                turns.append(
                    f"Engineer (unverified): <engineer_input>{_untrusted(eng_msg)}</engineer_input>"
                )
                turns.append(f"Your response: {agent_resp}")
            context_block += "\n\n### Conversation History\n" + "\n\n".join(turns)

        return self._create_agent(self._system_prompt + context_block, _FollowUpAnswer)

    def _model(self) -> Groq:
        return Groq(id=self.MODEL_ID, temperature=TEMPERATURE, max_tokens=MAX_OUTPUT_TOKENS)


def _validate_evidence(diagnosis: AgentDiagnosis, allowed_ids: set[str]) -> AgentDiagnosis:
    """Deterministic evidence validator.

    Validates that every cited ``evidence_references`` was actually part
    of the investigation's evidence set::

        evidence_references in allowed_ids

    ``allowed_ids`` contains the runbook IDs that were retrieved by the
    orchestrator's pipeline (candidates) plus any IDs discovered by the
    agent's tool calls.  A reference that exists in the repository but
    was NOT part of this investigation is stripped — the model cannot
    cherry-pick arbitrary runbooks from the database.

    This is stronger than a simple existence check because it prevents
    *unsupported reasoning* (citing a valid but irrelevant runbook),
    not just *citation hallucination* (citing a non-existent runbook).

    The free-text fields get the same check: anything shaped like a citable
    ID that is not in ``allowed_ids`` is replaced by ``_REMOVED_CITATION``,
    so an invented ID cannot reach the engineer through the prose either.
    """
    known = {_canonical_id(ref): ref for ref in allowed_ids}
    cited = [known.get(_canonical_id(ref)) for ref in diagnosis.evidence_references]
    unsupported = [
        ref for ref, match in zip(diagnosis.evidence_references, cited, strict=True) if not match
    ]
    update: dict[str, object] = {
        "evidence_references": list(dict.fromkeys(match for match in cited if match))
    }
    for name in ("root_cause", "reasoning", "recommendation"):
        update[name], removed = _scrub_citations(getattr(diagnosis, name), known)
        unsupported.extend(removed)
    if unsupported:
        logger.warning(
            "Evidence validator stripped %d unsupported reference(s): %s "
            "(not in investigation evidence set)",
            len(unsupported),
            unsupported,
        )
    return diagnosis.model_copy(update=update)


# Models often write typographic hyphens (gpt-oss uses U+2011) inside IDs.
_HYPHENS = "-\u2010\u2011\u2012\u2013\u2014\u2015\u2212"
# Anything shaped like a citable ID: runbook IDs and compatibility evidence IDs.
_CITATION_PATTERN = re.compile(rf"\b(?:RB|compat)(?:[{_HYPHENS}][A-Z0-9]+)+\b", re.IGNORECASE)
_REMOVED_CITATION = "[unverified reference removed]"


def _canonical_id(ref: str) -> str:
    """Compare IDs regardless of hyphen style and case: RB-* upper, everything else lower."""
    ascii_ref = re.sub(f"[{_HYPHENS}]", "-", ref.strip())
    return ascii_ref.upper() if ascii_ref[:3].upper() == "RB-" else ascii_ref.lower()


def _scrub_citations(text: str, known: dict[str, str]) -> tuple[str, list[str]]:
    removed: list[str] = []

    def replace(match: re.Match[str]) -> str:
        ref = match.group(0)
        if _canonical_id(ref) in known:
            return ref
        removed.append(ref)
        return _REMOVED_CITATION

    return _CITATION_PATTERN.sub(replace, text), removed


# Formatting Helpers


def _raise_on_failed_run(response: object) -> None:
    """Agno reports provider errors (rate limits, bad requests) as the run *content*
    with status ERROR instead of raising; never let that pass as an answer."""
    if getattr(response, "status", None) == RunStatus.error:
        raise RuntimeError(f"LLM provider error: {getattr(response, 'content', '')}")


def _total_tokens(response: object) -> int | None:
    """Read total token usage from an Agno run response, if the model reported it."""
    total = getattr(getattr(response, "metrics", None), "total_tokens", None)
    return total if isinstance(total, int) else None


def _format_diagnosis(diagnosis: AgentDiagnosis) -> str:
    """Format a validated ``AgentDiagnosis`` as a human-readable string.

    This is the final output sent through the AgentPort protocol to the
    orchestrator and eventually to the engineer via the UI.
    """
    evidence = ", ".join(diagnosis.evidence_references) if diagnosis.evidence_references else "None"
    return (
        f"**Root Cause**: {diagnosis.root_cause}\n\n"
        f"**Confidence**: {diagnosis.confidence}\n\n"
        f"**Reasoning**: {diagnosis.reasoning}\n\n"
        f"**Recommendation**: {diagnosis.recommendation}\n\n"
        f"**Evidence**: {evidence}"
    )


def _build_user_message(
    component: str,
    causal_chains: list[list[GraphNode]],
    candidates: list[RetrievalCandidate],
) -> str:
    """Format the initial user message with pre-computed system evidence."""
    chain_text = _format_chains(causal_chains)
    candidates_text = _format_candidates(candidates)

    return (
        f"Investigate the following incident on component '{_untrusted(component)}'.\n\n"
        f"## System Evidence (verified)\n"
        f"<system_evidence>\n"
        f"### Observed Causal Chains\n{chain_text}\n\n"
        f"### Retrieved Historical Runbooks\n{candidates_text}\n"
        f"</system_evidence>\n\n"
        "Analyze the evidence above and provide your diagnosis."
    )


def _format_chains(causal_chains: list[list[GraphNode]]) -> str:
    """Format causal chains with full structural context for the LLM.

    Each node is expanded with: event_type, severity, component, timestamp,
    and description.  Edges show type and Δt between events.  This gives the
    LLM the graph structure it needs for causal reasoning, not just a flat
    string of event names.
    """
    if not causal_chains:
        return "No causal chains identified."

    blocks: list[str] = []
    for i, chain in enumerate(causal_chains, 1):
        block_lines = [f"Chain {i} ({len(chain)} events):"]
        for j, node in enumerate(chain):
            evt = node.event
            ts = evt.timestamp.strftime("%H:%M:%S")
            comp = _untrusted(evt.related_component or "unknown")
            block_lines.append(
                f"  [{j + 1}] {evt.event_type.value} [{evt.severity.value}]\n"
                f"      component={comp}, time={ts}\n"
                f"      {_untrusted(evt.description[:200])}"
            )
            # Show the edge to the next node
            if j < len(chain) - 1:
                next_node = chain[j + 1]
                dt = (next_node.event.timestamp - evt.timestamp).total_seconds()
                block_lines.append(f"        --[ Δt={dt:.1f}s ]-->")
        blocks.append("\n".join(block_lines))

    return "\n\n".join(blocks)


def _format_candidates(candidates: list[RetrievalCandidate]) -> str:
    """Format retrieval candidates for inclusion in the agent prompt.

    The ``score`` field in ``RetrievalCandidate`` is a **hybrid score**
    (fused from BM25 + dense + structural signals) produced by the
    orchestrator's retrieval pipeline.  It is labelled as such to avoid
    confusion with the semantic-only scores from the agent's tool.
    """
    if not candidates:
        return "No historical runbooks retrieved."
    lines: list[str] = []
    for c in candidates:
        lines.append(f"- {c.runbook_id} (hybrid_score: {c.score:.2f})")
    return "\n".join(lines)

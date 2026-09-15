"""DID-5/DID-7: AgnoGroqAgent — Multi-turn investigative agent with tool calling.

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
Groq / Llama-3.3-70b-versatile — ~750 tok/s inference speed matters when
an engineer is waiting for a diagnosis during an active incident.  The free
tier is sufficient for the project demo.
"""

from __future__ import annotations

import logging
import os
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from agno.agent import Agent
from agno.models.groq import Groq
from pydantic import BaseModel, Field

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


class AgentDiagnosis(BaseModel):
    """Validated structured output schema for agent diagnoses.
    Used as ``response_model``
    A deterministic evidence validator runs on top to strip any runbook IDs
    that the model hallucinated.
    """

    root_cause: str = Field(description="Concise identification of the root cause")
    confidence: str = Field(description="High, Medium, or Low")
    reasoning: str = Field(description="Step-by-step analysis of the evidence")
    recommendation: str = Field(description="Specific remediation action")
    evidence_references: list[str] = Field(
        default_factory=list,
        description="Runbook IDs cited as evidence (only IDs present in the system)",
    )


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


# Prompt Loading cached

_PROMPT_CACHE: str | None = None


def _load_system_prompt() -> str:
    """Load and cache the system prompt from ``prompts/agno_diagnosis.txt``."""
    global _PROMPT_CACHE  # noqa: PLW0603
    if _PROMPT_CACHE is not None:
        return _PROMPT_CACHE

    prompt_path = _repo_root() / "prompts" / "agno_diagnosis.txt"
    if not prompt_path.exists():
        raise FileNotFoundError(
            f"System prompt not found at {prompt_path}. "
            "Create prompts/agno_diagnosis.txt before initializing the agent."
        )
    _PROMPT_CACHE = prompt_path.read_text(encoding="utf-8")
    return _PROMPT_CACHE


def _repo_root() -> Path:
    """Walk up from this file to find the repo root (contains pyproject.toml)."""
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "pyproject.toml").exists():
            return parent
    raise RuntimeError("Could not locate repo root (no pyproject.toml in ancestors)")


# Tools validate input before execution and truncate outputs to
# prevent context-window blowup.

_TOOL_OUTPUT_MAX_CHARS = 1500
_TOOL_QUERY_MIN_CHARS = 3
_TOOL_QUERY_MAX_CHARS = 500


def _make_search_runbooks_tool(
    chroma: ChromaRunbookClient,
    runbook_repo: JSONRunbookRepository,
    retrieved_ids: set[str],
) -> Callable[..., str]:
    """Create a ``search_runbooks`` tool bound to *chroma* and *runbook_repo*.

    Note: this tool uses **semantic-only** search via ChromaDB, not the full
    HybridRetriever pipeline (BM25 + Dense + Causal matching).  This is by
    design — the full hybrid retrieval is the *orchestrator's* job (it runs
    before the agent to make the Tier 2/3 gate decision).  The agent's tool
    is for **additional** lightweight searches during investigation, not a
    replacement for the hybrid pipeline.

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
            Formatted list of matching runbooks with summaries, causal chains,
            and fixes.
        """
        query = query.strip()
        if len(query) < _TOOL_QUERY_MIN_CHARS:
            return "Error: query must be at least 3 characters."
        query = query[:_TOOL_QUERY_MAX_CHARS]

        logger.info("Tool call: search_runbooks(query=%r)", query[:80])

        hits = chroma.search(query, top_k=3)
        if not hits:
            return "No matching runbooks found for this query."

        # Output construction with size limit
        lines: list[str] = []
        for hit in hits[:3]:
            detail = runbook_repo.get_by_id(hit.runbook_id)
            if detail:
                # Register this ID so evidence validator accepts it.
                retrieved_ids.add(detail.runbook_id)
                lines.append(f"[{detail.runbook_id}] (semantic_score: {hit.semantic_score:.2f})")
                lines.append(f"  Summary: {detail.summary[:200]}")
                lines.append(f"  Root cause: {detail.root_cause[:150]}")
                if detail.causal_chain:
                    lines.append(f"  Causal chain: {' -> '.join(detail.causal_chain)}")
                lines.append(f"  Fix: {detail.fix[:150]}")
                if detail.affected_components:
                    lines.append(f"  Affected: {', '.join(detail.affected_components)}")
                lines.append("")

        output = "\n".join(lines)
        return output[:_TOOL_OUTPUT_MAX_CHARS] if lines else "No detailed info available."

    return search_runbooks


def _make_check_dependencies_tool(
    registry: ComponentRegistry,
    retrieved_ids: set[str],
) -> Callable[..., str]:
    """Create a ``check_component_dependencies`` tool bound to *registry*."""

    def check_component_dependencies(component: str) -> str:
        """Check the software state and compatibility constraints of a component.

        Use this when you need to verify if a component has dependency conflicts,
        version mismatches, or missing requirements that could explain a crash.
        This tool performs a deterministic check against known constraints.

        Args:
            component: The name of the component (e.g., 'payment-service').

        Returns:
            A formatted compatibility report with an evidence ID.
        """
        logger.info("Tool call: check_component_dependencies(component=%r)", component)

        report = registry.check_compatibility(component)

        # Register the evidence ID so the validator accepts it.
        retrieved_ids.add(report.evidence_id)

        lines = [
            f"Compatibility Evidence: {report.evidence_id}",
            f"Component: {report.component_name}",
            f"Status: {report.status.value.upper()}",
            "",
            "Installed Versions:",
        ]

        for pkg, ver in report.installed_versions.items():
            lines.append(f"  {pkg}: {ver}")

        if report.violations:
            lines.append("\nViolations Found:")
            for v in report.violations:
                lines.append(f"  - {v}")

        return "\n".join(lines)

    return check_component_dependencies


def _make_get_runbook_detail_tool(
    runbook_repo: JSONRunbookRepository,
) -> Callable[..., str]:
    """Create a ``get_runbook_detail`` tool bound to *runbook_repo*.

    This is the "expensive / detailed" half of the progressive retrieval
    pattern::

        search_runbooks  → cheap, broad candidate discovery
        get_runbook_detail → specific, full evidence for a known ID
    """

    def get_runbook_detail(runbook_id: str) -> str:
        """Get the full details and fix commands of a specific runbook.

        Use this when you already know a runbook ID from the initial evidence
        and need the complete remediation procedure, including commands.

        Args:
            runbook_id: The runbook identifier (e.g. 'rb_payment_db_timeout').

        Returns:
            Full runbook details including fix commands and causal chain.
        """
        # Input validation
        runbook_id = runbook_id.strip()
        if not runbook_id.startswith("rb_"):
            return "Error: invalid runbook_id format. Must start with 'rb_'."

        logger.info("Tool call: get_runbook_detail(runbook_id=%r)", runbook_id)

        detail = runbook_repo.get_by_id(runbook_id)
        if not detail:
            return f"Runbook '{runbook_id}' not found in the historical database."

        output = (
            f"Runbook: {detail.runbook_id}\n"
            f"Incident: {detail.incident_id}\n"
            f"Summary: {detail.summary}\n"
            f"Root Cause: {detail.root_cause}\n"
            f"Causal Chain: {' -> '.join(detail.causal_chain) if detail.causal_chain else 'N/A'}\n"
            f"Fix: {detail.fix}\n"
            f"Commands: {'; '.join(detail.fix_commands) if detail.fix_commands else 'None'}\n"
            f"Affected Components: {', '.join(detail.affected_components)}"
        )
        return output[:_TOOL_OUTPUT_MAX_CHARS]

    return get_runbook_detail


# Agent

_MAX_FOLLOW_UP_TURNS = 5


class AgnoGroqAgent:
    """Multi-turn investigative agent for AIOps incident diagnosis.

    Implements the ``AgentPort`` protocol (investigation_orchestrator.py).
    The orchestrator calls ``diagnose()`` in Tier 3 with pre-computed
    evidence.  The agent synthesises the evidence into a **validated**
    structured diagnosis, optionally using tools for deeper investigation,
    and supports multi-turn follow-up with engineers.

    Diagnosis pipeline::

        LLM (with tools)
              ↓
        AgentDiagnosis (Pydantic response_model)
              ↓
        Evidence validator (deterministic — strips hallucinated IDs)
              ↓
        Formatted string for AgentPort

    Parameters
    ----------
    chroma_client:
        Optional.  ChromaDB client for semantic runbook search (tool dep).
    runbook_repo:
        Optional.  JSON runbook repository for full runbook data (tool dep
        + evidence validation).

    When tool dependencies are **not** provided the agent operates in
    summarisation-only mode (no tool calling, no evidence validation).
    This preserves backward compatibility with the current orchestrator.
    """

    MODEL_ID = "llama-3.3-70b-versatile"

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
        self._system_prompt = _load_system_prompt()

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
    ) -> str:
        """Produce a validated, grounded diagnosis from pre-computed evidence.

        Pipeline:  LLM → AgentDiagnosis → evidence validator → formatted str.

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

        # ── Step 1: Run agent with structured output ─────────────────
        agent = self._create_structured_agent()
        response = agent.run(user_message)

        # Agno returns content as Any when output_model is set;
        # at runtime it will be an AgentDiagnosis instance.
        if not isinstance(response.content, AgentDiagnosis):
            msg = f"Expected AgentDiagnosis, got {type(response.content).__name__}"
            raise TypeError(msg)
        diagnosis: AgentDiagnosis = response.content

        # Step 2: Evidence validation (deterministic)
        diagnosis = self._validate_evidence(diagnosis, allowed_ids)

        # Step 3: Format for AgentPort
        formatted = _format_diagnosis(diagnosis)

        # Store session context for follow-ups
        self._sessions[session_id] = _SessionContext(
            component=component,
            initial_evidence=user_message,
            diagnosis_text=formatted,
            allowed_evidence_ids=allowed_ids,
        )
        self._last_session_id = session_id

        logger.info(
            "Diagnosis complete: session=%s, length=%d chars",
            session_id,
            len(formatted),
        )
        return formatted

    # Multi-turn follow-up
    def follow_up(self, session_id: str, message: str) -> str:
        """Continue an investigation with additional context from the engineer.

        Engineer-provided context is explicitly framed as **unverified** in
        the agent prompt.  The agent retains the initial diagnosis and all
        prior follow-up turns as conversation context.

        Parameters
        ----------
        session_id:
            Identifier from ``last_session_id`` after calling ``diagnose``.
        message:
            Follow-up from the engineer (e.g. "we deployed v2.3 yesterday").

        Returns
        -------
        Updated analysis incorporating the new context.

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
            return "Please provide additional context or a specific question."

        if ctx.turn_count >= _MAX_FOLLOW_UP_TURNS:
            return (
                f"Maximum follow-up turns ({_MAX_FOLLOW_UP_TURNS}) reached for "
                f"this session.  Please start a new investigation."
            )

        ctx.turn_count += 1

        logger.info(
            "Follow-up: session=%s, turn=%d, message_length=%d",
            session_id,
            ctx.turn_count,
            len(message),
        )

        # Frame engineer input as unverified
        framed_message = (
            "## Engineer-Provided Context (UNVERIFIED)\n"
            "The following was provided by the on-call engineer.  It has "
            "NOT been verified against the IncidentGraph or FSM.  Treat "
            "it as a hypothesis to investigate, not as confirmed evidence.\n\n"
            f"Engineer: {message}"
        )

        # Lazily create follow-up agent with full context
        if ctx.followup_agent is None:
            ctx.followup_agent = self._create_followup_agent(ctx)

        try:
            response = ctx.followup_agent.run(framed_message)
            result: str = (
                response.content if response.content else "Agent returned an empty response."
            )

            ctx.followup_history.append((message, result))

            logger.info("Follow-up complete: session=%s, turn=%d", session_id, ctx.turn_count)
            return result

        except Exception as exc:
            logger.exception("Follow-up failed: session=%s", session_id)
            raise RuntimeError(f"Follow-up failed: {exc}") from exc

    # Properties

    @property
    def last_session_id(self) -> str | None:
        """Session ID of the most recent ``diagnose()`` call.

        Use this to pass to ``follow_up()`` for multi-turn conversations.
        """
        return self._last_session_id

    # Internal

    def _create_structured_agent(self) -> Agent:
        """Agent with ``output_model`` for validated structured output."""
        return Agent(
            model=Groq(id=self.MODEL_ID),
            tools=self._tools or None,
            instructions=self._system_prompt,
            output_model=AgentDiagnosis,
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
                turns.append(f"Engineer (unverified): {eng_msg}")
                turns.append(f"Your response: {agent_resp}")
            context_block += "\n\n### Conversation History\n" + "\n\n".join(turns)

        return Agent(
            model=Groq(id=self.MODEL_ID),
            tools=self._tools or None,
            instructions=self._system_prompt + context_block,
            markdown=False,
        )

    def _validate_evidence(
        self,
        diagnosis: AgentDiagnosis,
        allowed_ids: set[str],
    ) -> AgentDiagnosis:
        """Deterministic evidence validator.

        Validates that every cited ``evidence_references`` was actually part
        of the investigation's evidence set::

            evidence_references ⊆ allowed_ids

        ``allowed_ids`` contains the runbook IDs that were retrieved by the
        orchestrator's pipeline (candidates) plus any IDs discovered by the
        agent's tool calls.  A reference that exists in the repository but
        was NOT part of this investigation is stripped — the model cannot
        cherry-pick arbitrary runbooks from the database.

        This is stronger than a simple existence check because it prevents
        *unsupported reasoning* (citing a valid but irrelevant runbook),
        not just *citation hallucination* (citing a non-existent runbook).
        """
        if not diagnosis.evidence_references:
            return diagnosis

        # If tool deps were injected, tool calls may have discovered new IDs.
        # _tool_retrieved_ids is populated by the search tool at call time.
        effective_allowed = allowed_ids | self._tool_retrieved_ids

        valid: list[str] = []
        unsupported: list[str] = []

        for ref in diagnosis.evidence_references:
            if ref in effective_allowed:
                valid.append(ref)
            else:
                unsupported.append(ref)

        if unsupported:
            logger.warning(
                "Evidence validator stripped %d unsupported reference(s): %s "
                "(not in investigation evidence set)",
                len(unsupported),
                unsupported,
            )
            return diagnosis.model_copy(update={"evidence_references": valid})

        return diagnosis


# Formatting Helpers


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
        f"Investigate the following incident on component '{component}'.\n\n"
        f"## System Evidence (verified)\n\n"
        f"### Observed Causal Chains\n{chain_text}\n\n"
        f"### Retrieved Historical Runbooks\n{candidates_text}\n\n"
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
            comp = evt.related_component or "unknown"
            block_lines.append(
                f"  [{j + 1}] {evt.event_type.value} [{evt.severity.value}]\n"
                f"      component={comp}, time={ts}\n"
                f"      {evt.description[:200]}"
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

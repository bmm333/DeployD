"""DID-16: EventSimulator — end-to-end incident investigation pipeline.

Wires together:
    ScenarioLoader → FSM + IncidentGraph → CausalEngine
    → HybridRetriever → InvestigationOrchestrator → DiagnosisResult

Agent selection
---------------
If ``GROQ_API_KEY`` is set in the environment, the real ``AgnoGroqAgent`` is
used for Tier 3 diagnosis.  If the key is absent, a ``StubAgent`` is used
instead.  In both cases the mode is printed to stdout — there is no silent
fallback.

Usage
-----
The simulator is not meant to be used directly; see ``__main__.py`` for the
CLI entrypoint.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import TYPE_CHECKING

from deployd.adapters.incoming.simulator.scenario_loader import ScenarioLoader
from deployd.adapters.outgoing.vector_store.bm25_index import BM25RunbookIndex
from deployd.adapters.outgoing.vector_store.chroma_client import ChromaRunbookClient
from deployd.adapters.outgoing.vector_store.hybrid_retriever import HybridRetriever
from deployd.adapters.outgoing.vector_store.runbook_repository import JSONRunbookRepository
from deployd.application.dtos.diagnosis import AgentDiagnosis, TierDiagnosisResult
from deployd.application.dtos.investigation_request import InvestigationRequest
from deployd.application.orchestrators.investigation_orchestrator import (
    AgentPort,
    InvestigationOrchestrator,
)
from deployd.application.use_cases.retrieve_candidates import RetrieveCandidates
from deployd.domain.causal.causal_engine import CausalEngine

if TYPE_CHECKING:
    from pathlib import Path

    from deployd.application.dtos.retrieval import RetrievalCandidate
    from deployd.domain.graph.node import GraphNode

logger = logging.getLogger(__name__)

# ── Stub AgentPort ────────────────────────────────────────────────────────────


class _StubAgent:
    """Offline stub satisfying AgentPort. Used when GROQ_API_KEY is absent."""

    @property
    def last_session_id(self) -> str | None:
        return None

    def diagnose(
        self,
        component: str,
        causal_chains: list[list[GraphNode]],
        candidates: list[RetrievalCandidate],
    ) -> AgentDiagnosis:
        chain_len = sum(len(c) for c in causal_chains)
        best = candidates[0] if candidates else None
        rb_ref = best.runbook_id if best else "no-runbook"
        return AgentDiagnosis(
            root_cause=f"[STUB] Deploy-triggered failure on {component} ({chain_len} events in causal chain)",
            confidence="Medium",
            reasoning="[STUB] No LLM was called. GROQ_API_KEY not set.",
            recommendation=f"[STUB] Review {rb_ref} and compare with current state.",
            evidence_references=[rb_ref] if best else [],
        )

    def follow_up(self, session_id: str, message: str) -> AgentDiagnosis:
        return AgentDiagnosis(
            root_cause=f"[STUB] follow_up not available in offline mode (session={session_id}).",
            confidence="Low",
            reasoning="[STUB] No LLM was called. GROQ_API_KEY not set.",
            recommendation="Start a new investigation or use the live agent for follow-up.",
            evidence_references=[],
        )


# ── SimulatorResult ───────────────────────────────────────────────────────────


@dataclass
class SimulatorResult:
    """Full output of one simulator run."""

    scenario_id: str
    component: str
    expected_tier: str
    agent_mode: str  # "live/AgnoGroqAgent" or "offline/stub"
    diagnosis: TierDiagnosisResult


# ── EventSimulator ────────────────────────────────────────────────────────────


class EventSimulator:
    """End-to-end scenario runner.

    Parameters
    ----------
    data_dir:
        Path to the project ``data/`` directory.  Used to locate runbooks
        (``data/runbooks/``), Chroma DB (``data/chroma/``), and scenarios
        (``data/scenarios/``).
    confidence_threshold:
        Threshold forwarded to HybridRetriever and used by the orchestrator
        to decide between Tier 2 and Tier 3.
    """

    def __init__(
        self,
        data_dir: Path,
        confidence_threshold: float = 0.5,
    ) -> None:
        self._data_dir = data_dir
        self._threshold = confidence_threshold
        self._loader = ScenarioLoader()

    def run(self, scenario_path: Path) -> SimulatorResult:
        """Run a single scenario end-to-end.

        Parameters
        ----------
        scenario_path:
            Absolute path to the scenario JSON file.

        Returns
        -------
        SimulatorResult with the DiagnosisResult and metadata.
        """
        # ── Step 1: Load scenario ────────────────────────────────────────────
        definition, raw_events_meta = self._loader.load_with_meta(scenario_path)
        logger.info("Loaded scenario: %s (%s)", definition.scenario_id, scenario_path.name)

        # ── Step 2: Build graph + replay FSM ────────────────────────────────
        graph_result = self._loader.build_graph(definition, raw_events_meta)
        logger.info(
            "Graph built: %d nodes, FSM state=%s",
            len(graph_result.graph.nodes),
            graph_result.fsm_state.value,
        )

        # ── Step 3: Build causal chains ──────────────────────────────────────
        engine = CausalEngine(graph_result.graph)
        chains: list[list[GraphNode]] = []
        for root in graph_result.graph.get_root_nodes():
            chains.extend(engine.causal_chain(root.node_id))
        logger.info("CausalEngine found %d chain(s)", len(chains))

        # ── Step 4: Hybrid retrieval ─────────────────────────────────────────
        retrieval_result = self._build_retriever().execute(
            query=self._loader.resolve_query(definition),
            top_k=5,
        )
        logger.info(
            "Retrieval: %d candidates, strong=%s",
            len(retrieval_result.candidates),
            retrieval_result.has_strong_match,
        )

        # ── Step 5: Select AgentPort ──────────────────────────────────────────
        agent, agent_mode = self._select_agent()

        # ── Step 6: Orchestrate ───────────────────────────────────────────────
        orchestrator = InvestigationOrchestrator(agent=agent)
        request = InvestigationRequest(
            component=definition.component,
            graph=graph_result.graph,
            fsm_state=graph_result.fsm_state,
            retrieval_result=retrieval_result,
        )
        diagnosis = orchestrator.run(request)

        return SimulatorResult(
            scenario_id=definition.scenario_id,
            component=definition.component,
            expected_tier=definition.expected_tier,
            agent_mode=agent_mode,
            diagnosis=diagnosis,
        )

    # ── Internals ─────────────────────────────────────────────────────────────

    def _build_retriever(self) -> RetrieveCandidates:
        """Bootstrap the retrieval stack from the data directory."""
        runbooks_dir = self._data_dir / "runbooks"
        chroma_dir = str(self._data_dir / "chroma")

        repo = JSONRunbookRepository(runbooks_dir)

        chroma = ChromaRunbookClient(persist_directory=chroma_dir)
        bm25 = BM25RunbookIndex()

        # Index all runbooks into Chroma and BM25
        all_runbooks = repo.list_all()
        bm25.build([(rb.runbook_id, rb.summary) for rb in all_runbooks])
        for rb in all_runbooks:
            chroma.index_runbook(rb.runbook_id, rb.summary, {"tags": rb.tags})

        retriever = HybridRetriever(
            chroma=chroma,
            bm25=bm25,
            confidence_threshold=self._threshold,
        )
        return RetrieveCandidates(retriever=retriever)

    def _select_agent(self) -> tuple[AgentPort, str]:
        """Return (agent, mode_label). Transparent about which agent is used."""
        api_key = os.getenv("GROQ_API_KEY", "").strip()
        if api_key:
            from deployd.adapters.outgoing.ai.agno_agent import AgnoGroqAgent
            from deployd.adapters.outgoing.vector_store.bm25_index import BM25RunbookIndex
            from deployd.adapters.outgoing.vector_store.chroma_client import ChromaRunbookClient
            from deployd.adapters.outgoing.vector_store.runbook_repository import (
                JSONRunbookRepository,
            )
            from deployd.domain.components.component_registry import (
                ComponentRegistry,  # noqa: TCH002
            )

            runbooks_dir = self._data_dir / "runbooks"
            chroma_dir = str(self._data_dir / "chroma")
            components_path = self._data_dir / "components.json"
            constraints_path = self._data_dir / "compatibility_constraints.json"

            repo = JSONRunbookRepository(runbooks_dir)
            chroma = ChromaRunbookClient(persist_directory=chroma_dir)
            bm25 = BM25RunbookIndex()
            all_runbooks = repo.list_all()
            bm25.build([(rb.runbook_id, rb.summary) for rb in all_runbooks])

            from deployd.adapters.outgoing.registry.json_component_repository import (
                JSONComponentRepository,
            )

            component_registry: ComponentRegistry = JSONComponentRepository(
                components_file=components_path,
                constraints_file=constraints_path,
            )

            agent = AgnoGroqAgent(
                chroma_client=chroma,
                runbook_repo=repo,
                component_registry=component_registry,
            )
            return agent, "live/AgnoGroqAgent"

        return _StubAgent(), "offline/stub — GROQ_API_KEY not set"

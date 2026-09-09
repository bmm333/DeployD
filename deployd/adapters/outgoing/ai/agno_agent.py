"""
Impl of AgentPort using agno framework with groq as backend inference
Simply choosed groq over gemini for this use case:
 -Groq's LPU hardware runs llama-3.3-70b-v at 750ish tokens per second vs 50ish for comparable hosted models
 for our use case where the engineer is waiting for a diagnosis, i think this matters a lot.
 -Cost wise: It's free tier is enough for our demo.
 -Model: as mentioned llama 3.3 70b is strong general model, more than sufficent for the structured
 summarization of causal chains and runbook evidence, no hardcore reasing heavy task.

Gemini used as a fallback in case of any downtime of groq.

NOTE THIS ACTUAL IMPLEMENTATION IS NOT FINAL IN ANY WAY ITS JUST A FIRST BASE TO WORK ON. DO NOT PULL THIS IF YOU WANT TO RUN, FIRST PLEASE MODIFY IT OTHERWISE IT WILL NOT RUN

"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from agno.agent import Agent
from agno.models.groq import Groq

if TYPE_CHECKING:
    from pathlib import Path

    from deployd.application.dtos.retrieval import RetrievalCandidate
    from deployd.domain.graph.node import GraphNode


class AgnoGroqAgent:
    """new agent per each call, it makes sens when it recived the call to keep sessions separate
    but we might need multiple turn takes, since the engineer can observe an error , and the agent might not
    find it one shot. FOR NOW ITS NO MULTI TURN"""

    MODEL_ID = "llama-3.3-70b-versatile"

    # will test the 8b version as well of 3.1
    def diagnose(
        self,
        component: str,
        causal_chains: list[list[GraphNode]],
        candidates: list[RetrievalCandidate],
    ) -> str:
        api_key = os.environ.get("GROQ_API_KEY")
        if not api_key:
            raise RuntimeError(
                """
                    GROQ_API_KEY not set
                """
            )
        prompt = self._build_prompt(component, causal_chains, candidates)

        try:
            agent = Agent(
                model=Groq(id=self.MODEL_ID),
                # no tools needed only a summarization
                tools=[],
                markdown=False,
            )
            response = agent.run(prompt)
            return response.content or "Agent returned an empty response"

        except Exception as exc:
            raise RuntimeError(f"Agent Call failed: {exc}") from exc

    def _build_prompt(
        self,
        component: str,
        causal_chains: list[list[GraphNode]],
        candidates: list[RetrievalCandidate],
    ) -> str:
        prompt_path = _repo_root() / "prompts" / "agno_diagnosis.txt"
        template = prompt_path.read_text(encoding="utf-8")

        chain_text = _format_chains(causal_chains)
        runbooks_text = _format_candidates(candidates)

        return str(
            template.format(
                component=component, causal_chains=chain_text, retrieved_runbooks=runbooks_text
            )
        )


# helper functions


# Walking up
def _repo_root() -> Path:
    from pathlib import Path

    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "pyproject.toml").exists():
            return parent
    raise RuntimeError("Could not find repo root (no pyproject.toml found)")


def _format_chains(causal_chains: list[list[GraphNode]]) -> str:
    if not causal_chains:
        return "No causal chain identified."
    lines = []
    for i, chain in enumerate(causal_chains, 1):
        steps = " -> ".join(
            f"{node.event.event_type.value}[{node.event.severity.value}]" for node in chain
        )
        lines.append(f"Chain {i}: {steps}")
    return "\n".join(lines)


def _format_candidates(candidates: list[RetrievalCandidate]) -> str:
    if not candidates:
        return "No historical runbooks retrieved."
    lines = []
    for c in candidates:
        lines.append(f"- {c.runbook_id} (score: {c.score:.2f})")
    return "\n".join(lines)

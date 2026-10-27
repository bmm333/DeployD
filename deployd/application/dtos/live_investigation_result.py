from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from deployd.application.dtos.diagnosis import AgentDiagnosis, DiagnosisTier


@dataclass(frozen=True)
class LiveInvestigationResult:
    component: str
    query: str
    tier: DiagnosisTier
    chain: tuple[str, ...]
    chain_components: tuple[str, ...]
    rules_fired: tuple[str, ...]
    candidates: Sequence[Any]
    threshold: float
    breakdown: dict[str, dict[str, float]]
    diagnosis: AgentDiagnosis | None
    llm_usage: int | None
    llm_error: str | None
    answer_discarded: str | None
    agent_available: bool
    summary: str

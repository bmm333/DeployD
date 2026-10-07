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
    rules_fired: tuple[str, ...]
    candidates: Sequence[Any]
    breakdown: dict[str, dict[str, float]]
    diagnosis: AgentDiagnosis | None
    llm_usage: dict[str, int] | None
    llm_error: str | None
    agent_available: bool
    summary: str

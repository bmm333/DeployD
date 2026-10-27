"""Pydantic schemas for the agent's read-only tools (DID-33).

Each tool validates its arguments with an ``*Input`` model and answers with a
result model serialised as JSON.  JSON is also what contains untrusted runbook
text: a string value cannot close itself or add fields, so whatever a runbook
says stays inside the field it came from.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

QUERY_MIN_CHARS = 3
QUERY_MAX_CHARS = 500


class _Schema(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class RunbookSearchInput(_Schema):
    query: str = Field(min_length=QUERY_MIN_CHARS)

    @field_validator("query", mode="before")
    @classmethod
    def _normalise(cls, value: object) -> object:
        return value.strip()[:QUERY_MAX_CHARS] if isinstance(value, str) else value


class RunbookDetailInput(_Schema):
    runbook_id: str = Field(pattern=r"^RB-[A-Z0-9-]{1,80}$")

    @field_validator("runbook_id", mode="before")
    @classmethod
    def _normalise(cls, value: object) -> object:
        return value.strip().upper() if isinstance(value, str) else value


class ComponentInput(_Schema):
    """Argument of the component tools (dependency check, FSM health)."""

    component: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,63}$")

    @field_validator("component", mode="before")
    @classmethod
    def _normalise(cls, value: object) -> object:
        return value.strip().lower() if isinstance(value, str) else value


class RunbookSummary(_Schema):
    runbook_id: str
    semantic_score: float
    summary: str
    root_cause: str
    causal_chain: list[str]
    fix: str
    affected_components: list[str]


class RunbookSearchResult(_Schema):
    runbooks: list[RunbookSummary]


class RunbookDetail(_Schema):
    runbook_id: str
    incident_id: str
    summary: str
    root_cause: str
    causal_chain: list[str]
    fix: str
    fix_commands: list[str]
    affected_components: list[str]


class DependencyCheckResult(_Schema):
    evidence_id: str
    component: str
    status: Literal["COMPATIBLE", "INCOMPATIBLE", "UNKNOWN"]
    installed_versions: dict[str, str]
    violations: list[str]


class HealthTransition(_Schema):
    at: str
    from_state: str
    to_state: str


class FsmHealthResult(_Schema):
    component: str
    state: Literal["HEALTHY", "DEGRADED", "CRASHING", "RESTARTING", "CRASH_LOOP"]
    events_replayed: int
    restart_attempts: int
    transitions: list[HealthTransition]


class ToolError(_Schema):
    """Returned instead of a result: bad arguments, unknown IDs, oversized output."""

    error: str

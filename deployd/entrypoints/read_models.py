"""Response models of the read-only endpoints the MCP server consumes (DID-40)."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class _Schema(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class RunbookMatch(_Schema):
    runbook_id: str
    score: float  # fused hybrid score (ADR-007), not the semantic signal alone
    summary: str
    root_cause: str
    causal_chain: list[str]
    fix: str
    affected_components: list[str]


class RunbookMatches(_Schema):
    threshold: float  # a score at or above it is a strong match (the gate's Tier 3)
    runbooks: list[RunbookMatch]

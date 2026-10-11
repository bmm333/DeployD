"""Incident domain entity."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from deployd.domain.entities.core_event import Severity
from deployd.domain.entities.human_decision import HumanDecision


class Incident(BaseModel):  # type: ignore
    """A bounded episode of correlated anomalies, from first event to resolution."""

    id: uuid.UUID = Field(default_factory=uuid.uuid4)
    opened_at: datetime
    resolved_at: datetime | None = None
    status: Literal["OPEN", "RESOLVED"] = "OPEN"
    peak_severity: Severity = Severity.INFO
    graph_snapshot: dict[str, object] = Field(default_factory=lambda: {"nodes": [], "edges": []})  # type: ignore[arg-type]
    chat_history: list[dict[str, str]] = Field(default_factory=list)
    root_cause_summary: str | None = None
    decisions: list[HumanDecision] = Field(default_factory=list)  # audit trail (DID-38)

    model_config = {"frozen": False}

    def record_decision(self, decision: HumanDecision) -> None:
        """Append an engineer's decision; only the open incident is still under review."""
        if decision.incident_id != self.id:
            raise ValueError("the decision belongs to another incident")
        if self.status != "OPEN":
            raise ValueError("decisions are only recorded while the incident is open")
        self.decisions.append(decision)

    def resolve(
        self,
        resolved_at: datetime,
        graph_snapshot: dict[str, object],
        chat_history: list[dict[str, str]],
        root_cause_summary: str | None,
    ) -> None:
        self.resolved_at = resolved_at
        self.status = "RESOLVED"
        self.graph_snapshot = graph_snapshot
        self.chat_history = chat_history
        self.root_cause_summary = root_cause_summary

    def update_severity(self, severity: Severity) -> None:
        order = [Severity.INFO, Severity.WARNING, Severity.ERROR, Severity.CRITICAL]
        if order.index(severity) > order.index(self.peak_severity):
            self.peak_severity = severity

    @property
    def duration_seconds(self) -> float | None:
        if self.resolved_at is None:
            return None
        return (self.resolved_at - self.opened_at).total_seconds()

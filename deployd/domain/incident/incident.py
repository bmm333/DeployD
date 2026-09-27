"""Incident domain entity."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from deployd.domain.entities.core_event import Severity


class Incident(BaseModel):  # type: ignore[misc]
    """A bounded episode of correlated anomalies, from first event to resolution."""

    id: uuid.UUID = Field(default_factory=uuid.uuid4)
    opened_at: datetime
    resolved_at: datetime | None = None
    status: Literal["OPEN", "RESOLVED"] = "OPEN"
    peak_severity: Severity = Severity.INFO
    graph_snapshot: list[dict[str, object]] = Field(default_factory=list)
    chat_history: list[dict[str, str]] = Field(default_factory=list)
    root_cause_summary: str | None = None

    model_config = {"frozen": False}

    def resolve(
        self,
        resolved_at: datetime,
        graph_snapshot: list[dict[str, object]],
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

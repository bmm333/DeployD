"""Relationship rappresentation"""

from __future__ import annotations

import uuid  # noqa: TCH003

from pydantic import BaseModel, ConfigDict, Field

from deployd.domain.graph.edge_type import EdgeType  # noqa: TCH001


class GraphEdge(BaseModel):  # type: ignore[misc]
    model_config = ConfigDict(frozen=True)

    source: uuid.UUID
    target: uuid.UUID
    edge_type: EdgeType
    confidence: float = Field(ge=0.0, le=1.0)
    time_delta_seconds: float | None = Field(default=None, ge=0.0)
    rule_id: str | None = None

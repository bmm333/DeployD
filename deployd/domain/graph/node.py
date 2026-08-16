"""Node rappresents an observed domain event.
Node MUST BE TRACABLE BACK TO A CORE EVENT
"""

from __future__ import annotations

import uuid

from pydantic import BaseModel, ConfigDict, Field

from deployd.domain.entities.core_event import CoreEvent  # noqa: TCH001


class GraphNode(BaseModel):  # type: ignore[misc]
    model_config = ConfigDict(frozen=True)
    node_id: uuid.UUID = Field(default_factory=uuid.uuid4)
    event: CoreEvent


"""This is only a wrapper around CoreEvent not a separate concept"""

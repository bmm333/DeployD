"""Node rappresents an observed domain event.
Node MUST BE TRACABLE BACK TO A CORE EVENT
"""

from __future__ import annotations

import uuid  # noqa: TCH003

from pydantic import BaseModel, ConfigDict

from deployd.domain.entities.core_event import CoreEvent  # noqa: TCH001


class GraphNode(BaseModel):  # type: ignore[misc]
    model_config = ConfigDict(frozen=True)
    event: CoreEvent

    @property
    def node_id(self) -> uuid.UUID:
        return self.event.event_id

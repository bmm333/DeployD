import zlib, json, uuid
from pydantic import BaseModel
from typing import Literal, Dict, Any
from datetime import datetime
from domain.entities import CoreEvent, CoreEventType

EventSource = Literal["metrics", "deploy", "logs", "infra"]
EventType = Literal["cpu_high", "latency_high", "error_spike", "deploy_started"]

class IncomingEvent(BaseModel):
    event_id: Optional[str] = None
    timestamp: datetime
    source: EventSource
    type: EventType
    service: str
    payload: Dict[str, Any]
    trace_id: Optional[str] = None
    version: int = 1
    checksum: Optional[int] = None

    def compute_checksum(self):
        data = json.dumps({
            "timestamp": self.timestamp.isoformat(),
            "source": self.source,
            "type": self.type,
            "service": self.service,
            "payload": self.payload,
            "trace_id": self.trace_id,
            "version": self.version
        }, sort_keys=True)

        return zlib.crc32(data.encode())

    def finalize(self):
        if not self.event_id:
            self.event_id = str(uuid.uuid4())
        self.checksum = self.compute_checksum()
        return self

    def to_core_event(self) -> CoreEvent:

        core_type = CoreEventType.RESOURCE_EXHAUSTION if self.type == "cpu_high" else CoreEventType.PROCESS_ABNORMAL_EXIT
        return CoreEvent(
            event_id=self.event_id,
            timestamp=self.timestamp,
            source_identifier=self.service,
            core_type=core_type,
            metadata=self.payload
        )

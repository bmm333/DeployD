from pydantic import BaseModel
from typing import Literal, Dict, Any, Optional
from datetime import datetime
import zlib
import json
import uuid

EventSource = Literal["metrics", "deploy", "logs", "infra"]

EventType = Literal[
    "cpu_high",
    "latency_high",
    "error_rate_high",
    "deploy_started",
    "deploy_succeeded",
    "deploy_failed",
    "package_updated",
    "config_changed",
    "error_spike"
]

class Event(BaseModel):
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

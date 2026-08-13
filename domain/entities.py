from dataclasses import dataclass
from datetime import datetime
from typing import Dict, Any, Optimal
from enum import Enum

#Abstraction
class CoreEventType(Enum):
    PROCESS_ABNORMAL_EXIT="process_abnormal_exit"
    STATE_CHANGE="state_change"
    DEPENDENCY_FAILURE="dependency_failure"
    RESOURCE_EXHAUSTION="resource_exhaustion"

@dataclass(frozen=True) #immutable, its only a plain object
class CoreEvent:
    event_id: str
    timestamp: datetime
    source_identifier: str #can be the vm name, service name, or heck even cluster id.
    core_type: CoreEventType
    metadata: Dict[str,Any]


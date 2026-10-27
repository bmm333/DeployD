from __future__ import annotations

from enum import Enum

""" Current limitation: without defining the behaviour of process crash during restarting,the
FSM has no specific transtion for a failed restart attempt, making crash loop detection incomplete."""


class ProcessHealthStatus(str, Enum):
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    CRASHING = "CRASHING"
    RESTARTING = "RESTARTING"
    CRASH_LOOP = "CRASH_LOOP"

"""What changed, and when: the vocabulary RULE-07 and RULE-08 share (DID-42, DID-48).

The identity of a change is the ``change_kind`` / ``change_ref`` the collector put in the event
metadata, never free text. A failure is a symptom: the event types and the HTTP statuses that
say a component is not serving.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import datetime, timedelta

from deployd.domain.causal.config import CorrelationConfig
from deployd.domain.entities.core_event import (
    CHANGE_EVENT_TYPES,
    CoreEvent,
    CoreEventType,
    Severity,
)

# A change is identified by what it is, not by which entity reports it.
ChangeSignature = tuple[str, str]

_NAMESPACE = uuid.UUID("9a1c4c1e-6d0b-4a53-8a0e-2f6b7d8c0e55")

_FAILURE_TYPES = frozenset(
    {
        CoreEventType.DEPENDENCY_FAILURE,
        CoreEventType.CONNECTIVITY_LOSS,
        CoreEventType.PROCESS_CRASH,
        CoreEventType.HEALTH_CHECK_FAIL,
    }
)

# One observation per entity every few minutes, unless the changes in force differ.
OBSERVATION_GAP = timedelta(minutes=5)


def label(signature: ChangeSignature) -> str:
    kind, ref = signature
    return f"{kind} {ref}" if ref else kind


def signature(event: CoreEvent) -> ChangeSignature | None:
    kind = event.metadata.get("change_kind")
    if event.event_type not in CHANGE_EVENT_TYPES or not isinstance(kind, str) or not kind:
        return None
    ref = event.metadata.get("change_ref")
    return kind, ref if isinstance(ref, str) else ""


def is_failure(event: CoreEvent, config: CorrelationConfig) -> bool:
    if event.event_type in _FAILURE_TYPES:
        return True
    if event.event_type is not CoreEventType.STATE_CHANGE:
        return False
    status = config.get_metric(event.metadata, "status_code")
    return isinstance(status, int) and status >= 500


def active_changes(
    window: Sequence[CoreEvent], entity: str, at: datetime
) -> frozenset[ChangeSignature]:
    """For each kind of change, the value in force on *entity* at *at* (the latest report)."""
    latest: dict[str, tuple[datetime, str]] = {}
    for e in window:
        sig = signature(e)
        if sig is None or e.related_component != entity or e.timestamp > at:
            continue
        kind, ref = sig
        if kind not in latest or e.timestamp >= latest[kind][0]:
            latest[kind] = (e.timestamp, ref)
    return frozenset((kind, ref) for kind, (_, ref) in latest.items())


def observed_changes(observation: CoreEvent) -> frozenset[ChangeSignature]:
    recorded = observation.metadata.get("active_changes")
    if not isinstance(recorded, list):
        return frozenset()
    return frozenset((str(pair[0]), str(pair[1])) for pair in recorded if len(pair) == 2)


def observation_event(
    event: CoreEvent, window: Sequence[CoreEvent], config: CorrelationConfig
) -> CoreEvent | None:
    """A long-lived record of the changes in force when *event*, a failure, happened.

    The window forgets failures after minutes; contrasting an entity with itself over hours
    needs to remember under which changes it was failing. Thinned: nothing is recorded while
    the last observation is recent and the changes in force are the same.
    """
    entity = event.related_component
    if entity is None or not is_failure(event, config):
        return None
    # Also recorded when no change is in force: a failure that happened before a change
    # must keep that change from being implicated.
    state = active_changes(window, entity, event.timestamp)
    previous = [
        e
        for e in window
        if e.event_type is CoreEventType.SYMPTOM_OBSERVED and e.related_component == entity
    ]
    if previous:
        last = max(previous, key=lambda e: e.timestamp)
        if event.timestamp - last.timestamp < OBSERVATION_GAP and observed_changes(last) == state:
            return None
    return CoreEvent(
        event_id=uuid.uuid5(_NAMESPACE, str(event.event_id)),
        event_type=CoreEventType.SYMPTOM_OBSERVED,
        timestamp=event.timestamp,
        severity=Severity.INFO,
        related_component=entity,
        description=f"{entity} failing under {', '.join(label(s) for s in sorted(state)) or 'no recorded change'}",
        metadata={
            "active_changes": [list(s) for s in sorted(state)],
            "source_event_id": str(event.event_id),
        },
    )

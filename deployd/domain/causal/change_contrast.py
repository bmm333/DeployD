"""RULE-08: change contrast over time, for an entity without peers (DID-48).

RULE-07 compares an entity with its peers. A lone instance has none, so it is compared with
itself: the changes in force at each of its failures are recorded (``SYMPTOM_OBSERVED``), and
a change that is constant across every failure is implicated, while one whose value changed
and left the entity failing (a release rolled back, symptoms unchanged) is ruled out.

Honest limit: two changes that become effective at the same moment (new code and new system
libraries at one restart) are both constant across every failure; they are listed as not
separable yet, until something varies. The rule names no package, OS or vendor.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from deployd.domain.causal.changes import (
    ChangeSignature,
    is_failure,
    label,
    observed_changes,
    signature,
)
from deployd.domain.causal.config import CorrelationConfig
from deployd.domain.entities.core_event import CoreEvent, CoreEventType, Severity

RULE_ID = "RULE-08-CHANGE-CONTRAST"

_NAMESPACE = uuid.UUID("2b8e4f6a-7c1d-4e0b-9a35-6d4c1f0b8e77")


@dataclass(frozen=True)
class ChangeContrast:
    """The changes constant across every failure of an entity, and the ones that varied."""

    entity: str
    implicated: dict[ChangeSignature, datetime]  # when each was first reported
    varied: tuple[str, ...]  # e.g. "deploy 2.14.0 -> 2.13.9"
    first_symptom: CoreEvent


def _has_peers(event: CoreEvent, window: Sequence[CoreEvent]) -> bool:
    group = event.metadata.get("peer_group")
    if not isinstance(group, str) or not group:
        return False
    members = {e.related_component for e in window if e.metadata.get("peer_group") == group}
    return len(members - {None}) >= 2


def _implicated(
    observations: Sequence[CoreEvent], reported: dict[ChangeSignature, datetime]
) -> dict[ChangeSignature, datetime]:
    if not observations:
        return {}
    first = observations[0].timestamp
    states = [observed_changes(o) for o in observations]
    return {
        sig: ts for sig, ts in reported.items() if ts <= first and all(sig in s for s in states)
    }


def _varied(observations: Sequence[CoreEvent]) -> tuple[str, ...]:
    refs: dict[str, list[str]] = {}
    for observation in observations:
        for kind, ref in sorted(observed_changes(observation)):
            seen = refs.setdefault(kind, [])
            if ref not in seen:
                seen.append(ref)
    return tuple(f"{kind} {' -> '.join(r)}" for kind, r in sorted(refs.items()) if len(r) > 1)


def find_change_contrast(
    event: CoreEvent, window: Sequence[CoreEvent], config: CorrelationConfig
) -> ChangeContrast | None:
    """Evaluate *event* when it completes a failure cluster, or when it narrows the candidates."""
    entity = event.related_component
    if entity is None or not is_failure(event, config) or _has_peers(event, window):
        return None

    own = [
        e
        for e in window
        if e.related_component == entity
        and e.event_type is not CoreEventType.SYMPTOM_OBSERVED
        and is_failure(e, config)
    ]
    observations = sorted(
        (
            e
            for e in window
            if e.event_type is CoreEventType.SYMPTOM_OBSERVED and e.related_component == entity
        ),
        key=lambda e: e.timestamp,
    )
    reported: dict[ChangeSignature, datetime] = {}
    for e in window:
        sig = signature(e)
        if sig is not None and e.related_component == entity and e.timestamp <= event.timestamp:
            reported.setdefault(sig, e.timestamp)

    current = _implicated(observations, reported)
    if not current:
        return None

    completed = len(own) == config.thresholds.error_rate and own[-1].event_id == event.event_id
    fresh = observations[-1].metadata.get("source_event_id") == str(event.event_id)
    narrowed = fresh and set(_implicated(observations[:-1], reported)) != set(current)
    if not (completed or narrowed):
        return None
    return ChangeContrast(
        entity=entity,
        implicated=current,
        varied=_varied(observations),
        first_symptom=own[0] if completed else event,
    )


def contrast_event(found: ChangeContrast) -> CoreEvent:
    """The finding as a derived event; deterministic id, so re-evaluation is idempotent."""
    # closest to the first failure first: the likeliest trigger of an ordinary reading
    ordered = sorted(found.implicated, key=lambda s: found.implicated[s], reverse=True)
    names = ", ".join(label(s) for s in ordered)
    varied = "; ".join(found.varied)
    if len(ordered) == 1:
        description = f"{found.entity}: the only change constant across every failure is {names}"
    else:
        description = f"{found.entity}: every failure happened under {names}, not separable yet"
    if varied:
        description += f"; {varied} changed while it kept failing, so it cannot be the difference"
    key = "|".join([found.entity, *sorted(label(s) for s in found.implicated), varied])
    return CoreEvent(
        event_id=uuid.uuid5(_NAMESPACE, key),
        event_type=CoreEventType.CHANGE_CONTRAST_DETECTED,
        timestamp=min(found.implicated.values()),
        severity=Severity.ERROR,
        related_component=found.entity,
        description=description,
        metadata={
            "implicated_changes": [label(s) for s in ordered],
            "varied_changes": list(found.varied),
            "rule_id": RULE_ID,
        },
    )

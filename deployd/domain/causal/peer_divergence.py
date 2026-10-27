"""RULE-07: peer divergence (DID-42, DID-44).

When some members of a peer group fail and the others do not, the cause is a change that
separates the two groups, not one every member received. The rule compares *who got which
change* with *who is failing*; it names no package, OS or vendor: the identity of a change
is the ``change_kind`` / ``change_ref`` the collector put in the event metadata.

A peer group is whatever the collector tags (``metadata.peer_group``: an auto scaling group,
a target group, a Beanstalk environment, a workload). Nothing lists the members: they are the
components seen with the tag, so instances can come and go.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass

from deployd.domain.causal.changes import ChangeSignature, is_failure, label
from deployd.domain.causal.changes import signature as change_signature
from deployd.domain.causal.config import CorrelationConfig
from deployd.domain.entities.core_event import (
    CHANGE_EVENT_TYPES,
    CoreEvent,
    CoreEventType,
    Severity,
)

RULE_ID = "RULE-07-PEER-DIVERGENCE"

_NAMESPACE = uuid.UUID("5d0f7a52-8a53-4a53-9c5b-3f1e6f0d9b11")


@dataclass(frozen=True)
class PeerDivergence:
    """Which members fail, which do not, and the changes that separate them."""

    group: str
    affected: tuple[str, ...]
    healthy: tuple[str, ...]
    separating: dict[ChangeSignature, CoreEvent]  # earliest report among the affected
    excluded: tuple[ChangeSignature, ...]  # changes every observed member received
    first_symptoms: dict[str, CoreEvent]  # per affected member


def find_peer_divergence(
    event: CoreEvent,
    window: Sequence[CoreEvent],
    config: CorrelationConfig,
) -> PeerDivergence | None:
    """Evaluate *event* as the symptom that completes a failure cluster on one member."""
    host, group = event.related_component, event.metadata.get("peer_group")
    if host is None or not isinstance(group, str) or not group or not is_failure(event, config):
        return None

    failures: dict[str, list[CoreEvent]] = {}
    observed: set[str] = set()  # members seen doing something other than being changed
    reported: dict[ChangeSignature, dict[str, CoreEvent]] = {}
    for e in window:
        member = e.related_component
        if member is None or e.metadata.get("peer_group") != group:
            continue
        sig = change_signature(e)
        if sig is not None:
            if e.timestamp <= event.timestamp:  # a later change cannot be the cause
                reported.setdefault(sig, {}).setdefault(member, e)
        elif e.event_type not in CHANGE_EVENT_TYPES:
            observed.add(member)
            if is_failure(e, config):
                failures.setdefault(member, []).append(e)

    threshold = config.thresholds.error_rate
    own = failures.get(host, [])
    # Fire once per cluster, when it forms, not on every further error.
    if len(own) != threshold or own[-1].event_id != event.event_id:
        return None

    clustered = {m for m, found in failures.items() if len(found) >= threshold}
    healthy = observed - set(failures)  # silence is not health: a healthy one was seen working
    if not healthy:
        return None

    # A change explains the divergence when every clustered failure carries it and no healthy
    # member does. Members with a few errors that carry it count as affected; a few errors on
    # one that does not carry it are noise, neither affected nor healthy.
    separating: dict[ChangeSignature, CoreEvent] = {}
    affected: set[str] = set()
    for sig, members in reported.items():
        carriers = {
            m for m in failures if m in members and members[m].timestamp <= failures[m][0].timestamp
        }
        if clustered <= carriers and not members.keys() & healthy:
            separating[sig] = min((members[m] for m in carriers), key=lambda e: e.timestamp)
            affected |= carriers
    if not separating:
        return None

    everyone = affected | healthy
    return PeerDivergence(
        group=group,
        affected=tuple(sorted(affected)),
        healthy=tuple(sorted(healthy)),
        separating=separating,
        excluded=tuple(
            sorted(sig for sig, members in reported.items() if everyone <= members.keys())
        ),
        first_symptoms={m: failures[m][0] for m in sorted(affected)},
    )


def divergence_event(found: PeerDivergence) -> CoreEvent:
    """The finding as a derived event; deterministic id, so re-evaluation is idempotent."""
    separating = ", ".join(label(s) for s in sorted(found.separating))
    excluded = ", ".join(label(s) for s in found.excluded)
    total = len(found.affected) + len(found.healthy)
    description = (
        f"peer group {found.group}: {', '.join(found.affected)} fail, {len(found.healthy)} "
        f"others healthy; only {separating} separates them ({len(found.affected)}/"
        f"{len(found.affected)} vs 0/{len(found.healthy)})"
        + (f"; {excluded} reached all {total}, so it cannot explain the split" if excluded else "")
    )
    key = "|".join([found.group, *found.affected, separating])
    return CoreEvent(
        event_id=uuid.uuid5(_NAMESPACE, key),
        event_type=CoreEventType.PEER_DIVERGENCE_DETECTED,
        timestamp=min(e.timestamp for e in found.separating.values()),
        severity=Severity.ERROR,
        related_component=found.group,
        description=description,
        metadata={
            "peer_group": found.group,
            "affected": list(found.affected),
            "healthy": list(found.healthy),
            "separating_changes": [label(s) for s in sorted(found.separating)],
            "excluded_changes": [label(s) for s in found.excluded],
            "source_event_ids": [str(e.event_id) for e in found.separating.values()],
            "rule_id": RULE_ID,
        },
    )

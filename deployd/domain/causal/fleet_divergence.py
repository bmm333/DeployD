"""RULE-07: fleet divergence (DID-42).

When some instances of a fleet fail and their siblings do not, the cause is a change that
separates the two groups, not one every instance received. The rule compares *who got which
change* with *who is failing*; it names no package, OS or vendor: the identity of a change
is the ``change_kind`` / ``change_ref`` the collector put in the event metadata.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass

from deployd.domain.causal.config import CorrelationConfig
from deployd.domain.causal.topology import Topology
from deployd.domain.entities.core_event import (
    CHANGE_EVENT_TYPES,
    CoreEvent,
    CoreEventType,
    Severity,
)

RULE_ID = "RULE-07-FLEET-DIVERGENCE"

_NAMESPACE = uuid.UUID("5d0f7a52-8a53-4a53-9c5b-3f1e6f0d9b11")

_FAILURE_TYPES = frozenset(
    {
        CoreEventType.DEPENDENCY_FAILURE,
        CoreEventType.CONNECTIVITY_LOSS,
        CoreEventType.PROCESS_CRASH,
        CoreEventType.HEALTH_CHECK_FAIL,
    }
)

# A change is identified by what it is, not by which host reports it.
ChangeSignature = tuple[str, str]


@dataclass(frozen=True)
class FleetDivergence:
    """Which instances fail, which do not, and the changes that separate them."""

    fleet: str
    affected: tuple[str, ...]
    healthy: tuple[str, ...]
    separating: dict[ChangeSignature, CoreEvent]  # earliest report among the affected
    excluded: tuple[ChangeSignature, ...]  # changes every observed instance received
    first_symptoms: dict[str, CoreEvent]  # per affected instance


def label(signature: ChangeSignature) -> str:
    kind, ref = signature
    return f"{kind} {ref}" if ref else kind


def _is_failure(event: CoreEvent, config: CorrelationConfig) -> bool:
    if event.event_type in _FAILURE_TYPES:
        return True
    if event.event_type is not CoreEventType.STATE_CHANGE:
        return False
    status = config.get_metric(event.metadata, "status_code")
    return isinstance(status, int) and status >= 500


def _signature(event: CoreEvent) -> ChangeSignature | None:
    kind = event.metadata.get("change_kind")
    if event.event_type not in CHANGE_EVENT_TYPES or not isinstance(kind, str) or not kind:
        return None
    ref = event.metadata.get("change_ref")
    return kind, ref if isinstance(ref, str) else ""


def find_fleet_divergence(
    event: CoreEvent,
    window: Sequence[CoreEvent],
    config: CorrelationConfig,
    topology: Topology,
) -> FleetDivergence | None:
    """Evaluate *event* as the symptom that completes a failure cluster on one instance."""
    host = event.related_component
    fleet = topology.fleet_of(host)
    if host is None or len(fleet) < 2 or not _is_failure(event, config):
        return None

    failures: dict[str, list[CoreEvent]] = {}
    observed: set[str] = set()  # instances seen doing something other than being changed
    reported: dict[ChangeSignature, dict[str, CoreEvent]] = {}
    for e in window:
        member = e.related_component
        if member is None or member not in fleet:
            continue
        signature = _signature(e)
        if signature is not None:
            if e.timestamp <= event.timestamp:  # a later change cannot be the cause
                reported.setdefault(signature, {}).setdefault(member, e)
        elif e.event_type not in CHANGE_EVENT_TYPES:
            observed.add(member)
            if _is_failure(e, config):
                failures.setdefault(member, []).append(e)

    threshold = config.thresholds.error_rate
    own = failures.get(host, [])
    # Fire once per cluster, when it forms, not on every further error.
    if len(own) != threshold or own[-1].event_id != event.event_id:
        return None

    affected = {m for m, found in failures.items() if len(found) >= threshold}
    healthy = observed - set(failures)  # silence is not health: a healthy one was seen working
    if not healthy:
        return None

    separating = {
        sig: min((members[m] for m in affected), key=lambda e: e.timestamp)
        for sig, members in reported.items()
        if affected <= members.keys()
        and not members.keys() & healthy
        and all(members[m].timestamp <= failures[m][0].timestamp for m in affected)
    }
    if not separating:
        return None

    everyone = affected | healthy
    return FleetDivergence(
        fleet=topology.fleets[host],
        affected=tuple(sorted(affected)),
        healthy=tuple(sorted(healthy)),
        separating=separating,
        excluded=tuple(
            sorted(sig for sig, members in reported.items() if everyone <= members.keys())
        ),
        first_symptoms={m: failures[m][0] for m in sorted(affected)},
    )


def divergence_event(found: FleetDivergence) -> CoreEvent:
    """The finding as a derived event; deterministic id, so re-evaluation is idempotent."""
    separating = ", ".join(label(s) for s in sorted(found.separating))
    excluded = ", ".join(label(s) for s in found.excluded)
    total = len(found.affected) + len(found.healthy)
    description = (
        f"fleet {found.fleet}: {len(found.affected)}/{total} instances fail, "
        f"{len(found.healthy)} healthy; only change separating them: {separating} "
        f"({len(found.affected)}/{len(found.affected)} failing, 0/{len(found.healthy)} healthy)"
        + (f"; {excluded} on all {total}, excluded" if excluded else "")
    )
    key = "|".join([found.fleet, *found.affected, separating])
    return CoreEvent(
        event_id=uuid.uuid5(_NAMESPACE, key),
        event_type=CoreEventType.FLEET_DIVERGENCE_DETECTED,
        timestamp=min(e.timestamp for e in found.separating.values()),
        severity=Severity.ERROR,
        related_component=found.fleet,
        description=description,
        metadata={
            "fleet": found.fleet,
            "affected": list(found.affected),
            "healthy": list(found.healthy),
            "separating_changes": [label(s) for s in sorted(found.separating)],
            "excluded_changes": [label(s) for s in found.excluded],
            "source_event_ids": [str(e.event_id) for e in found.separating.values()],
            "rule_id": RULE_ID,
        },
    )

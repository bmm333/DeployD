"""RULE-08: an entity without peers is compared with itself over time."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from deployd.domain.causal.causal_rule import rule_change_contrast
from deployd.domain.causal.change_contrast import find_change_contrast
from deployd.domain.causal.changes import (
    OBSERVATION_GAP,
    active_changes,
    observation_event,
    observed_changes,
)
from deployd.domain.causal.config import CorrelationConfig
from deployd.domain.causal.topology import Topology
from deployd.domain.entities.core_event import CoreEvent, CoreEventType, Severity

T0 = datetime(2026, 10, 8, 2, 0, tzinfo=timezone.utc)
CONFIG = CorrelationConfig()  # error_rate 3
HOST = "web-09"


def _at(minutes: float) -> datetime:
    return T0 + timedelta(minutes=minutes)


def _change(minutes: float, kind: str, ref: str, host: str = HOST, **tags: str) -> CoreEvent:
    return CoreEvent(
        event_type=CoreEventType.DEPLOY_COMPLETED
        if kind == "deploy"
        else CoreEventType.CONFIG_CHANGE,
        timestamp=_at(minutes),
        severity=Severity.INFO,
        related_component=host,
        description=f"{kind} {ref} on {host}",
        metadata={"change_kind": kind, "change_ref": ref, **tags},
    )


def _error(minutes: float, host: str = HOST, **tags: str) -> CoreEvent:
    return CoreEvent(
        event_type=CoreEventType.STATE_CHANGE,
        timestamp=_at(minutes),
        severity=Severity.ERROR,
        related_component=host,
        description=f"{host} answered 500",
        metadata={"status_code": 500, **tags},
    )


def _ingest(events: list[CoreEvent]) -> list[CoreEvent]:
    """What the use case does: window plus a thinned observation per failure."""
    window: list[CoreEvent] = []
    for event in sorted(events, key=lambda e: e.timestamp):
        window.append(event)
        observation = observation_event(event, window, CONFIG)
        if observation is not None:
            window.append(observation)
    return window


def _fire(events: list[CoreEvent]):  # noqa: ANN202
    """Evaluate the rule on the last failure, with everything before it in the window."""
    window = _ingest(events)
    failure = max(
        (e for e in events if e.metadata.get("status_code") == 500), key=lambda e: e.timestamp
    )
    seen = [e for e in window if e.timestamp <= failure.timestamp]
    return rule_change_contrast(failure, seen, CONFIG, Topology())


def _first_failures() -> list[CoreEvent]:
    return [_error(600 + n * 0.1) for n in range(3)]


def test_two_changes_in_force_at_every_failure_are_both_implicated_and_not_separable() -> None:
    events = [
        _change(5, "os_upgrade", "run-1"),
        _change(590, "deploy", "2.14.0"),
        *_first_failures(),
    ]

    (match,) = _fire(events)

    cause = match.cause
    assert cause is not None
    assert cause.metadata["implicated_changes"] == ["deploy 2.14.0", "os_upgrade run-1"]
    assert "not separable yet" in cause.description
    assert match.rule_id == "RULE-08-CHANGE-CONTRAST"


def test_a_change_whose_value_varied_while_the_entity_kept_failing_is_ruled_out() -> None:
    events = [
        _change(5, "os_upgrade", "run-1"),
        _change(590, "deploy", "2.14.0"),
        *_first_failures(),
        _change(700, "deploy", "2.13.9"),  # the rollback
        _error(705),
    ]

    (match,) = _fire(events)

    cause = match.cause
    assert cause is not None
    assert cause.metadata["implicated_changes"] == ["os_upgrade run-1"]
    assert cause.metadata["varied_changes"] == ["deploy 2.14.0 -> 2.13.9"]
    assert "the only change constant across every failure is os_upgrade run-1" in cause.description
    assert "cannot be the difference" in cause.description


def test_the_finding_does_not_name_a_package_or_a_vendor() -> None:
    (match,) = _fire([_change(5, "os_upgrade", "run-1"), *_first_failures()])

    assert match.cause is not None
    assert "openssl" not in match.cause.description.lower()


def test_the_narrowing_is_reported_once_not_on_every_further_error() -> None:
    events = [
        _change(5, "os_upgrade", "run-1"),
        _change(590, "deploy", "2.14.0"),
        *_first_failures(),
        _change(700, "deploy", "2.13.9"),
        _error(705),
    ]
    window = _ingest(events)
    later = _error(706)
    window += [later, *[o for o in [observation_event(later, [*window, later], CONFIG)] if o]]

    assert rule_change_contrast(later, window, CONFIG, Topology()) == []


def test_a_change_reported_after_the_first_failure_is_not_a_candidate() -> None:
    events = [*_first_failures(), _change(610, "os_upgrade", "run-1"), _error(611)]

    assert _fire(events) == []


def test_without_any_change_there_is_nothing_to_contrast() -> None:
    assert _fire(_first_failures()) == []


def test_an_entity_with_peers_is_left_to_the_peer_rule() -> None:
    events = [
        _change(5, "os_upgrade", "run-1", peer_group="web"),
        *[_error(600 + n * 0.1, peer_group="web") for n in range(3)],
        _change(5, "os_upgrade", "run-1", host="web-01", peer_group="web"),
    ]
    healthy = CoreEvent(
        event_type=CoreEventType.HEALTH_CHECK_PASS,
        timestamp=_at(599),
        severity=Severity.INFO,
        related_component="web-01",
        description="web-01 healthy",
        metadata={"peer_group": "web"},
    )

    assert _fire([*events, healthy]) == []


def test_a_group_of_one_has_no_peers() -> None:
    events = [
        _change(5, "os_upgrade", "run-1", peer_group="web"),
        *[_error(600 + n * 0.1, peer_group="web") for n in range(3)],
    ]

    assert _fire(events) != []


def test_a_lone_error_does_not_complete_a_cluster() -> None:
    events = [_change(5, "os_upgrade", "run-1"), _error(600), _error(601)]
    window = _ingest(events)

    assert find_change_contrast(events[-1], window, CONFIG) is None


def test_the_same_candidates_always_yield_the_same_event_id() -> None:
    events = [_change(5, "os_upgrade", "run-1"), *_first_failures()]

    (first,) = _fire(events)
    (second,) = _fire(events)

    assert first.cause is not None and second.cause is not None
    assert first.cause.event_id == second.cause.event_id


def test_the_active_changes_are_the_latest_value_of_each_kind() -> None:
    window = [
        _change(5, "os_upgrade", "run-1"),
        _change(10, "deploy", "1"),
        _change(20, "deploy", "2"),
    ]

    assert active_changes(window, HOST, _at(15)) == {("os_upgrade", "run-1"), ("deploy", "1")}
    assert active_changes(window, HOST, _at(30)) == {("os_upgrade", "run-1"), ("deploy", "2")}
    assert active_changes(window, "other", _at(30)) == frozenset()


def test_observations_are_thinned_while_the_changes_in_force_stay_the_same() -> None:
    window = [_change(5, "os_upgrade", "run-1")]
    kept = []
    for minute in (600, 601, 602, 600 + OBSERVATION_GAP.total_seconds() / 60 + 1):
        failure = _error(minute)
        window.append(failure)
        observation = observation_event(failure, window, CONFIG)
        if observation is not None:
            window.append(observation)
            kept.append(observation)

    assert len(kept) == 2
    assert observed_changes(kept[0]) == {("os_upgrade", "run-1")}


def test_a_new_change_in_force_is_recorded_at_once() -> None:
    window = [_change(5, "os_upgrade", "run-1"), _change(590, "deploy", "1")]
    first = _error(600)
    window.append(first)
    window.append(observation_event(first, window, CONFIG))  # type: ignore[arg-type]  # recorded
    window.append(_change(600.5, "deploy", "2"))
    second = _error(601)
    window.append(second)

    observation = observation_event(second, window, CONFIG)

    assert observation is not None
    assert observed_changes(observation) == {("os_upgrade", "run-1"), ("deploy", "2")}

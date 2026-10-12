"""RULE-07: the change that separates failing members of a peer group from healthy ones."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from deployd.domain.causal.causal_rule import rule_peer_divergence
from deployd.domain.causal.config import CorrelationConfig
from deployd.domain.causal.peer_divergence import divergence_event, find_peer_divergence
from deployd.domain.causal.topology import Topology
from deployd.domain.entities.core_event import CoreEvent, CoreEventType, Severity

T0 = datetime(2026, 10, 8, 2, 0, tzinfo=timezone.utc)
HOSTS = [f"web-{i:02d}" for i in range(1, 11)]
BAD = ["web-03", "web-05", "web-07", "web-09"]
CONFIG = CorrelationConfig()  # error_rate 3


def _at(minutes: float) -> datetime:
    return T0 + timedelta(minutes=minutes)


def _change(
    host: str, minutes: float, kind: str, ref: str = "", group: str | None = "web"
) -> CoreEvent:
    return CoreEvent(
        event_type=CoreEventType.CONFIG_CHANGE
        if kind != "deploy"
        else CoreEventType.DEPLOY_COMPLETED,
        timestamp=_at(minutes),
        severity=Severity.INFO,
        related_component=host,
        description=f"{kind} {ref} applied on {host}",
        metadata={"change_kind": kind, "change_ref": ref, "peer_group": group},
    )


def _error(host: str, minutes: float, group: str | None = "web") -> CoreEvent:
    return CoreEvent(
        event_type=CoreEventType.STATE_CHANGE,
        timestamp=_at(minutes),
        severity=Severity.ERROR,
        related_component=host,
        description=f"{host} answered 500",
        metadata={"status_code": 500, "peer_group": group},
    )


def _ok(host: str, minutes: float, group: str | None = "web") -> CoreEvent:
    return CoreEvent(
        event_type=CoreEventType.HEALTH_CHECK_PASS,
        timestamp=_at(minutes),
        severity=Severity.INFO,
        related_component=host,
        description=f"{host} healthy",
        metadata={"peer_group": group},
    )


def _group_history(upgraded: list[str] = BAD) -> list[CoreEvent]:
    events = [_change(h, 5, "os_upgrade", "run-1") for h in upgraded]
    events += [_change(h, 600, "deploy", "2.14.0") for h in HOSTS]
    return events


def _incident(upgraded: list[str] = BAD, failing: list[str] = BAD) -> list[CoreEvent]:
    """Deploy at minute 600, then 3 errors on each failing host and a heartbeat on the others."""
    events = _group_history(upgraded)
    for i, host in enumerate(failing):
        events += [_error(host, 601 + i * 0.1 + n * 0.5) for n in range(3)]
    events += [_ok(h, 601) for h in HOSTS if h not in failing]
    return sorted(events, key=lambda e: e.timestamp)


def _fire(window: list[CoreEvent], last: CoreEvent | None = None):  # noqa: ANN202
    event = last or window[-1]
    return rule_peer_divergence(event, window, CONFIG, Topology())


def _last_error(window: list[CoreEvent]) -> CoreEvent:
    return max(
        (e for e in window if e.metadata.get("status_code") == 500), key=lambda e: e.timestamp
    )


def test_the_change_only_the_failing_instances_received_is_named_and_the_deploy_is_excluded() -> (
    None
):
    window = _incident()

    matches = _fire(window, _last_error(window))

    cause = matches[0].cause
    assert cause is not None
    assert cause.event_type is CoreEventType.PEER_DIVERGENCE_DETECTED
    assert cause.metadata["separating_changes"] == ["os_upgrade run-1"]
    assert cause.metadata["excluded_changes"] == ["deploy 2.14.0"]
    assert cause.metadata["affected"] == BAD
    assert "web-03, web-05, web-07, web-09 fail, 6 others healthy" in cause.description
    assert "os_upgrade run-1" in cause.description
    assert "deploy 2.14.0 reached all 10, so it cannot explain the split" in cause.description


def test_every_failing_instance_is_linked_to_the_one_finding() -> None:
    window = _incident()

    matches = _fire(window, _last_error(window))

    assert {m.trigger.related_component for m in matches} == set(BAD)
    assert len({m.cause.event_id for m in matches if m.cause}) == 1
    assert all(m.rule_id == "RULE-07-PEER-DIVERGENCE" for m in matches)


def test_the_finding_does_not_name_a_package_or_a_vendor() -> None:
    window = _incident()

    cause = _fire(window, _last_error(window))[0].cause

    assert cause is not None
    assert "openssl" not in cause.description.lower()


def test_a_change_every_instance_received_explains_nothing() -> None:
    window = _incident(upgraded=HOSTS)

    assert _fire(window, _last_error(window)) == []


def test_a_healthy_instance_with_the_same_change_makes_it_a_non_explanation() -> None:
    window = _incident(upgraded=[*BAD, "web-01"])

    assert _fire(window, _last_error(window)) == []


def test_without_a_healthy_sibling_there_is_nothing_to_compare_against() -> None:
    window = _incident(failing=HOSTS, upgraded=HOSTS)

    assert _fire(window, _last_error(window)) == []


def test_silence_is_not_health() -> None:
    window = [e for e in _incident() if e.event_type is not CoreEventType.HEALTH_CHECK_PASS]

    assert _fire(window, _last_error(window)) == []


def test_a_sibling_seen_only_through_its_deploy_is_not_observed_healthy() -> None:
    window = [
        e
        for e in _incident()
        if e.event_type is not CoreEventType.HEALTH_CHECK_PASS or e.related_component != "web-02"
    ]
    healthy = {
        e.related_component for e in window if e.event_type is CoreEventType.HEALTH_CHECK_PASS
    }

    assert "web-02" not in healthy
    found = find_peer_divergence(_last_error(window), window, CONFIG)
    assert found is not None and "web-02" not in found.healthy


def test_a_change_that_came_after_the_first_error_is_not_a_cause() -> None:
    window = _incident(upgraded=[])
    window += [_change(h, 602, "os_upgrade", "run-2") for h in BAD]
    window.sort(key=lambda e: e.timestamp)

    assert _fire(window, _last_error(window)) == []


def test_the_rule_fires_once_when_the_cluster_forms_not_on_every_further_error() -> None:
    window = _incident()
    errors = sorted(
        (e for e in window if e.metadata.get("status_code") == 500), key=lambda e: e.timestamp
    )
    first_two_of_the_last_host = [e for e in errors if e.related_component == "web-09"][:2]

    assert _fire(window, first_two_of_the_last_host[1]) == []

    window.append(_error("web-09", 610))
    assert _fire(window, window[-1]) == []


def test_an_instance_of_another_peer_group_is_not_compared() -> None:
    window = _incident()
    other = _error("worker-01", 602, group="worker")

    assert find_peer_divergence(other, [*window, other], CONFIG) is None


def test_an_instance_without_a_peer_group_has_no_peers() -> None:
    window = [
        e.model_copy(update={"metadata": {**e.metadata, "peer_group": None}}) for e in _incident()
    ]

    assert _fire(window, _last_error(window)) == []


def test_events_without_a_change_identity_are_ignored() -> None:
    anonymous = [
        e.model_copy(update={"metadata": {}}) if e.event_type is CoreEventType.CONFIG_CHANGE else e
        for e in _incident()
    ]

    assert _fire(anonymous, _last_error(anonymous)) == []


def test_the_same_divergence_always_yields_the_same_event_id() -> None:
    window = _incident()
    found = find_peer_divergence(_last_error(window), window, CONFIG)

    assert found is not None
    assert divergence_event(found).event_id == divergence_event(found).event_id
    assert divergence_event(found).timestamp == _at(5)


def test_instances_with_only_a_few_errors_that_carry_the_change_count_as_affected() -> None:
    """Only the last host to fail has a full cluster in the window; the others kept erroring."""
    window = _group_history()
    window += [_error(h, 601 + i) for i, h in enumerate(BAD[:-1])]
    window += [_error(BAD[-1], 605 + n * 0.1) for n in range(3)]
    window += [_ok(h, 604) for h in HOSTS if h not in BAD]
    window.sort(key=lambda e: e.timestamp)

    cause = _fire(window, _last_error(window))[0].cause

    assert cause is not None and cause.metadata["affected"] == BAD


def test_a_stray_error_on_an_instance_without_the_change_does_not_hide_the_finding() -> None:
    window = _incident()
    window.append(_error("web-02", 601.4))
    window.sort(key=lambda e: e.timestamp)

    cause = _fire(window, _last_error(window))[0].cause

    assert cause is not None
    assert cause.metadata["affected"] == BAD
    assert "web-02" not in cause.metadata["healthy"]  # type: ignore[operator]  # a list of names


def test_a_failing_cluster_the_change_does_not_explain_means_no_finding() -> None:
    window = _incident()
    window += [_error("web-02", 601.5 + n * 0.1) for n in range(3)]
    window.sort(key=lambda e: e.timestamp)

    assert _fire(window, _last_error(window)) == []


def _many(size: int, bad: int) -> list[CoreEvent]:
    """A peer group of *size* instances, the first *bad* of them upgraded and failing."""
    hosts = [f"i-{n:03d}" for n in range(size)]
    events = [_change(h, 5, "os_upgrade", "run-1") for h in hosts[:bad]]
    events += [_change(h, 600, "deploy", "2.14.0") for h in hosts]
    for i, host in enumerate(hosts[:bad]):
        events += [_error(host, 601 + i * 0.01 + n * 0.5) for n in range(3)]
    events += [_ok(h, 601) for h in hosts[bad:]]
    return sorted(events, key=lambda e: e.timestamp)


@pytest.mark.parametrize(("size", "bad"), [(2, 1), (3, 1), (10, 4), (50, 7)])
def test_the_number_of_instances_is_not_declared_anywhere(size: int, bad: int) -> None:
    window = _many(size, bad)

    cause = _fire(window, _last_error(window))[0].cause

    assert cause is not None
    assert len(cause.metadata["affected"]) == bad  # type: ignore[arg-type]  # a list of names
    assert len(cause.metadata["healthy"]) == size - bad  # type: ignore[arg-type]


def test_an_instance_that_appears_during_the_incident_is_a_peer() -> None:
    """Autoscaling adds an instance (with the release, without the upgrade) after the deploy."""
    window = _incident()
    window += [_change("web-11", 602, "deploy", "2.14.0"), _ok("web-11", 603)]
    window.sort(key=lambda e: e.timestamp)

    cause = _fire(window, _last_error(window))[0].cause

    assert cause is not None
    assert cause.metadata["healthy"] == [
        "web-01",
        "web-02",
        "web-04",
        "web-06",
        "web-08",
        "web-10",
        "web-11",
    ]

"""RULE-07: the change that separates failing instances from healthy ones."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from deployd.domain.causal.causal_rule import rule_fleet_divergence
from deployd.domain.causal.config import CorrelationConfig
from deployd.domain.causal.fleet_divergence import divergence_event, find_fleet_divergence
from deployd.domain.causal.topology import Topology
from deployd.domain.entities.core_event import CoreEvent, CoreEventType, Severity

T0 = datetime(2026, 10, 8, 2, 0, tzinfo=timezone.utc)
HOSTS = [f"web-{i:02d}" for i in range(1, 11)]
BAD = ["web-03", "web-05", "web-07", "web-09"]
TOPOLOGY = Topology.from_calls({"alb": HOSTS}, fleets={h: "web" for h in HOSTS})
CONFIG = CorrelationConfig()  # error_rate 3


def _at(minutes: float) -> datetime:
    return T0 + timedelta(minutes=minutes)


def _change(host: str, minutes: float, kind: str, ref: str = "") -> CoreEvent:
    return CoreEvent(
        event_type=CoreEventType.CONFIG_CHANGE
        if kind != "deploy"
        else CoreEventType.DEPLOY_COMPLETED,
        timestamp=_at(minutes),
        severity=Severity.INFO,
        related_component=host,
        description=f"{kind} {ref} applied on {host}",
        metadata={"change_kind": kind, "change_ref": ref},
    )


def _error(host: str, minutes: float) -> CoreEvent:
    return CoreEvent(
        event_type=CoreEventType.STATE_CHANGE,
        timestamp=_at(minutes),
        severity=Severity.ERROR,
        related_component=host,
        description=f"{host} answered 500",
        metadata={"status_code": 500},
    )


def _ok(host: str, minutes: float) -> CoreEvent:
    return CoreEvent(
        event_type=CoreEventType.HEALTH_CHECK_PASS,
        timestamp=_at(minutes),
        severity=Severity.INFO,
        related_component=host,
        description=f"{host} healthy",
    )


def _fleet_history(upgraded: list[str] = BAD) -> list[CoreEvent]:
    events = [_change(h, 5, "os_upgrade", "run-1") for h in upgraded]
    events += [_change(h, 600, "deploy", "2.14.0") for h in HOSTS]
    return events


def _incident(upgraded: list[str] = BAD, failing: list[str] = BAD) -> list[CoreEvent]:
    """Deploy at minute 600, then 3 errors on each failing host and a heartbeat on the others."""
    events = _fleet_history(upgraded)
    for i, host in enumerate(failing):
        events += [_error(host, 601 + i * 0.1 + n * 0.5) for n in range(3)]
    events += [_ok(h, 601) for h in HOSTS if h not in failing]
    return sorted(events, key=lambda e: e.timestamp)


def _fire(window: list[CoreEvent], last: CoreEvent | None = None):  # noqa: ANN202
    event = last or window[-1]
    return rule_fleet_divergence(event, window, CONFIG, TOPOLOGY)


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
    assert cause.event_type is CoreEventType.FLEET_DIVERGENCE_DETECTED
    assert cause.metadata["separating_changes"] == ["os_upgrade run-1"]
    assert cause.metadata["excluded_changes"] == ["deploy 2.14.0"]
    assert cause.metadata["affected"] == BAD
    assert "4/10 instances fail" in cause.description
    assert "os_upgrade run-1" in cause.description
    assert "deploy 2.14.0 on all 10, excluded" in cause.description


def test_every_failing_instance_is_linked_to_the_one_finding() -> None:
    window = _incident()

    matches = _fire(window, _last_error(window))

    assert {m.trigger.related_component for m in matches} == set(BAD)
    assert len({m.cause.event_id for m in matches if m.cause}) == 1
    assert all(m.rule_id == "RULE-07-FLEET-DIVERGENCE" for m in matches)


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
    found = find_fleet_divergence(_last_error(window), window, CONFIG, TOPOLOGY)
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


def test_an_instance_outside_any_fleet_is_not_compared() -> None:
    window = _incident()
    other = _error("worker-01", 602)

    assert find_fleet_divergence(other, [*window, other], CONFIG, TOPOLOGY) is None


def test_events_without_a_change_identity_are_ignored() -> None:
    anonymous = [
        e.model_copy(update={"metadata": {}}) if e.event_type is CoreEventType.CONFIG_CHANGE else e
        for e in _incident()
    ]

    assert _fire(anonymous, _last_error(anonymous)) == []


def test_the_same_divergence_always_yields_the_same_event_id() -> None:
    window = _incident()
    found = find_fleet_divergence(_last_error(window), window, CONFIG, TOPOLOGY)

    assert found is not None
    assert divergence_event(found).event_id == divergence_event(found).event_id
    assert divergence_event(found).timestamp == _at(5)

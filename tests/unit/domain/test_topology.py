"""Topology answers 'does A call B?' from declared dependencies only."""

from __future__ import annotations

from deployd.domain.causal.topology import Topology

TOPOLOGY = Topology.from_calls({"api-gateway": ["auth-service", "search-service"]})


def test_declared_call_is_found() -> None:
    assert TOPOLOGY.calls_component("api-gateway", "auth-service")


def test_direction_matters() -> None:
    assert not TOPOLOGY.calls_component("auth-service", "api-gateway")


def test_undeclared_or_unknown_components_are_not_linked() -> None:
    assert not TOPOLOGY.calls_component("api-gateway", "billing-batch")
    assert not TOPOLOGY.calls_component("unknown-service", "auth-service")


def test_missing_component_names_are_never_linked() -> None:
    assert not TOPOLOGY.calls_component(None, "auth-service")
    assert not TOPOLOGY.calls_component("api-gateway", "")


def test_empty_topology_links_nothing() -> None:
    assert not Topology().calls_component("api-gateway", "auth-service")


def test_a_fleet_is_the_set_of_components_declared_with_the_same_fleet_name() -> None:
    topology = Topology.from_calls(
        {"alb": ["web-01", "web-02", "web-03"]},
        fleets={"web-01": "web", "web-02": "web", "web-03": "web", "worker-01": "worker"},
    )

    assert topology.fleet_of("web-02") == frozenset({"web-01", "web-02", "web-03"})
    assert topology.fleet_of("worker-01") == frozenset({"worker-01"})


def test_services_that_share_a_caller_are_not_a_fleet() -> None:
    topology = Topology.from_calls({"api-gateway": ["auth-service", "search-service"]})

    assert topology.fleet_of("auth-service") == frozenset()


def test_a_component_outside_any_fleet_has_none() -> None:
    topology = Topology.from_calls({}, fleets={"web-01": "web"})

    assert topology.fleet_of("alb") == frozenset()
    assert topology.fleet_of(None) == frozenset()

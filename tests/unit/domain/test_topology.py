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

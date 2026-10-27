"""load_topology reads the declared ``calls`` of each component."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from deployd.adapters.outgoing.registry.json_topology import load_topology


def _write(tmp_path: Path, components: dict[str, object]) -> Path:
    path = tmp_path / "components.json"
    path.write_text(json.dumps(components))
    return path


def test_calls_become_topology_edges(tmp_path: Path) -> None:
    path = _write(tmp_path, {"api-gateway": {"calls": ["auth-service"]}, "auth-service": {}})

    topology = load_topology(path)

    assert topology.calls_component("api-gateway", "auth-service")
    assert not topology.calls_component("auth-service", "api-gateway")


def test_malformed_calls_are_rejected(tmp_path: Path) -> None:
    path = _write(tmp_path, {"api-gateway": {"calls": "auth-service"}})

    with pytest.raises(ValueError, match="api-gateway"):
        load_topology(path)


def test_repository_components_file_declares_scenario_dependencies() -> None:
    topology = load_topology(Path("data/components.json"))

    assert topology.calls_component("checkout-service", "payment-service")
    assert topology.calls_component("search-service", "elasticsearch-cluster")

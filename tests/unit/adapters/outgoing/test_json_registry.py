import json
from pathlib import Path

import pytest
from deployd.adapters.outgoing.registry.json_registry import JSONComponentRegistry
from deployd.domain.components.compatibility import CompatibilityStatus


@pytest.fixture
def mock_registry_files(tmp_path: Path) -> tuple[Path, Path]:
    components = {
        "test-service": {
            "environment": "test",
            "runtime": {"name": "python", "version": "3.11"},
            "frameworks": {"fastapi": "0.100.0"},
            "dependencies": {"pydantic": "2.0.0"},
        }
    }
    constraints = {
        "fastapi": {
            "0.100.0": {"requires": {"pydantic": ">=1.0.0"}, "conflicts": {"python": "<3.10"}}
        },
        "pydantic": {
            "2.0.0": {
                "requires": {"python": ">=3.12"}  # ntentional
            }
        },
    }

    comp_file = tmp_path / "components.json"
    cons_file = tmp_path / "constraints.json"

    with open(comp_file, "w") as f:
        json.dump(components, f)
    with open(cons_file, "w") as f:
        json.dump(constraints, f)

    return comp_file, cons_file


def test_json_registry_get_component_state(mock_registry_files: tuple[Path, Path]) -> None:
    comp_file, cons_file = mock_registry_files
    registry = JSONComponentRegistry(comp_file, cons_file)

    state = registry.get_component_state("test-service")
    assert state is not None
    assert state.component_name == "test-service"
    assert state.runtime.name == "python"
    assert state.runtime.version == "3.11"
    assert state.frameworks["fastapi"] == "0.100.0"

    assert registry.get_component_state("unknown-service") is None


def test_json_registry_check_compatibility(mock_registry_files: tuple[Path, Path]) -> None:
    comp_file, cons_file = mock_registry_files
    registry = JSONComponentRegistry(comp_file, cons_file)

    report = registry.check_compatibility("test-service")
    assert report.component_name == "test-service"
    assert report.status == CompatibilityStatus.INCOMPATIBLE
    assert any("pydantic" in v and "requires python >=3.12" in v for v in report.violations)


def test_json_registry_missing_component(mock_registry_files: tuple[Path, Path]) -> None:
    comp_file, cons_file = mock_registry_files
    registry = JSONComponentRegistry(comp_file, cons_file)

    report = registry.check_compatibility("unknown")
    assert report.status == CompatibilityStatus.UNKNOWN
    assert "Component not found" in report.violations[0]

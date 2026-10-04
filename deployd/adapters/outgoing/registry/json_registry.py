import json
from pathlib import Path
from typing import Any

from deployd.domain.components.compatibility import (
    CompatibilityEngine,
    CompatibilityReport,
    CompatibilityStatus,
)
from deployd.domain.components.component import ComponentState, RuntimeInfo


class JSONComponentRegistry:
    """JSON-backed implementation of ComponentRegistry."""

    def __init__(self, components_file: Path, constraints_file: Path | None = None) -> None:
        self._components_file = components_file
        self._constraints_file = constraints_file
        self._engine = self._init_engine()

    def _init_engine(self) -> CompatibilityEngine:
        constraints_db: dict[str, dict[str, Any]] = {}
        if self._constraints_file and self._constraints_file.exists():
            with open(self._constraints_file) as f:
                constraints_db = json.load(f)
        return CompatibilityEngine(constraints_db)

    def _load_components(self) -> dict[str, Any]:
        if not self._components_file.exists():
            return {}
        with open(self._components_file) as f:
            return json.load(f)  # type: ignore[no-any-return]

    def get_component_state(self, component_name: str) -> ComponentState | None:
        """Retrieve the known software state of a component."""
        components = self._load_components()
        if component_name not in components:
            return None

        data = components[component_name]
        runtime_data = data.get("runtime", {})
        return ComponentState(
            component_name=component_name,
            environment=data.get("environment", "unknown"),
            runtime=RuntimeInfo(
                name=runtime_data.get("name", "unknown"),
                version=runtime_data.get("version", "unknown"),
            ),
            frameworks=data.get("frameworks", {}),
            dependencies=data.get("dependencies", {}),
        )

    def check_compatibility(self, component_name: str) -> CompatibilityReport:
        """Run the deterministic compatibility engine for a component."""
        state = self.get_component_state(component_name)
        if not state:
            return CompatibilityReport(
                evidence_id=f"compat-{component_name}-001",
                component_name=component_name,
                status=CompatibilityStatus.UNKNOWN,
                installed_versions={},
                violations=["Component not found in registry."],
            )
        return self._engine.evaluate(state)

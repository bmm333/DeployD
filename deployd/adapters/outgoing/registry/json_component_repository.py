import json
import logging
from pathlib import Path

from deployd.domain.components.compatibility import (
    CompatibilityEngine,
    CompatibilityReport,
    CompatibilityStatus,
)
from deployd.domain.components.component import ComponentState, RawConstraintDict, RuntimeInfo
from deployd.domain.components.component_registry import ComponentRegistry

logger = logging.getLogger(__name__)


class JSONComponentRepository(ComponentRegistry):
    """Adapter that reads component state and compatibility constraints from JSON files."""

    def __init__(self, components_file: Path, constraints_file: Path):
        self._components_file = components_file
        self._constraints_file = constraints_file

        # Load component state
        self._components: dict[str, ComponentState] = {}
        self._load_components()

        # Load constraints and initialize engine
        constraints_data = self._load_constraints()
        self._engine = CompatibilityEngine(constraints_data)

    def _load_components(self) -> None:
        if not self._components_file.exists():
            logger.warning("Components file %s does not exist", self._components_file)
            return

        try:
            with open(self._components_file) as f:
                data = json.load(f)

            for name, comp_data in data.items():
                runtime_data = comp_data.get("runtime", {})
                runtime = RuntimeInfo(
                    name=runtime_data.get("name", "unknown"),
                    version=runtime_data.get("version", "unknown"),
                )

                state = ComponentState(
                    component_name=name,
                    environment=comp_data.get("environment", "unknown"),
                    runtime=runtime,
                    frameworks=comp_data.get("frameworks", {}),
                    dependencies=comp_data.get("dependencies", {}),
                )
                self._components[name] = state

            logger.info(
                "Loaded %d components from %s", len(self._components), self._components_file
            )
        except Exception as e:
            logger.error("Failed to load components: %s", e)

    def _load_constraints(self) -> dict[str, dict[str, RawConstraintDict]]:
        if not self._constraints_file.exists():
            logger.warning("Constraints file %s does not exist", self._constraints_file)
            return {}

        try:
            with open(self._constraints_file) as f:
                data = json.load(f)
            logger.info("Loaded compatibility constraints from %s", self._constraints_file)
            return data  # type: ignore[no-any-return]
        except Exception as e:
            logger.error("Failed to load constraints: %s", e)
            return {}

    def get_component_state(self, component_name: str) -> ComponentState | None:
        return self._components.get(component_name)

    def check_compatibility(self, component_name: str) -> CompatibilityReport:
        state = self.get_component_state(component_name)
        if not state:
            return CompatibilityReport(
                evidence_id=f"compat-{component_name}-001",
                component_name=component_name,
                status=CompatibilityStatus.UNKNOWN,
                installed_versions={},
                violations=[f"Component '{component_name}' not found in registry."],
            )

        return self._engine.evaluate(state)

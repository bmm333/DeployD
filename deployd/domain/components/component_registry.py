from typing import Protocol, runtime_checkable

from deployd.domain.components.compatibility import CompatibilityReport
from deployd.domain.components.component import ComponentState


@runtime_checkable
class ComponentRegistry(Protocol):
    """Port for interacting with component state and compatibility constraints."""

    def get_component_state(self, component_name: str) -> ComponentState | None:
        """Retrieve the known software state of a component."""
        ...

    def check_compatibility(self, component_name: str) -> CompatibilityReport:
        """Run the deterministic compatibility engine for a component."""
        ...

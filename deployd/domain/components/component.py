from __future__ import annotations

from typing import TypedDict

from pydantic import BaseModel, ConfigDict, Field


class RuntimeInfo(BaseModel):  # type: ignore[misc]
    model_config = ConfigDict(frozen=True)
    name: str
    version: str


class ComponentState(BaseModel):  # type: ignore[misc]
    """Domain representation of a component's software state."""

    model_config = ConfigDict(frozen=True)
    component_name: str
    environment: str
    runtime: RuntimeInfo
    frameworks: dict[str, str] = Field(default_factory=dict)
    dependencies: dict[str, str] = Field(default_factory=dict)

    @property
    def all_packages(self) -> dict[str, str]:
        """Returns a flat dictionary of all installed packages (runtime, frameworks, deps)."""
        packages = {self.runtime.name: self.runtime.version}
        packages.update(self.frameworks)
        packages.update(self.dependencies)
        return packages


class RawConstraintDict(TypedDict, total=False):
    """Type hint for the raw JSON constraint dictionaries."""

    requires: dict[str, str]
    conflicts: dict[str, str]

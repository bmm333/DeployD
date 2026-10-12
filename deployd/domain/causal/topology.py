"""Declared topology (who calls whom, which instances form a fleet), passed to rules."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Topology:
    """Declared dependencies (for each component, the components it calls) and fleets."""

    calls: Mapping[str, frozenset[str]] = field(default_factory=dict)
    fleets: Mapping[str, str] = field(default_factory=dict)  # component -> fleet name

    @classmethod
    def from_calls(
        cls, calls: Mapping[str, Iterable[str]], fleets: Mapping[str, str] | None = None
    ) -> Topology:
        return cls(
            {caller: frozenset(callees) for caller, callees in calls.items()}, dict(fleets or {})
        )

    def calls_component(self, caller: str | None, callee: str | None) -> bool:
        if not caller or not callee:
            return False
        return callee in self.calls.get(caller, frozenset())

    def fleet_of(self, component: str | None) -> frozenset[str]:
        """The members of *component*'s declared fleet (identical instances of one service),
        *component* included. A component outside any fleet has none."""
        name = self.fleets.get(component or "")
        if name is None:
            return frozenset()
        return frozenset(c for c, fleet in self.fleets.items() if fleet == name)

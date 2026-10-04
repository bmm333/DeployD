"""Service dependency topology, passed to correlation rules as a parameter."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Topology:
    """Declared dependencies: for each component, the components it calls."""

    calls: Mapping[str, frozenset[str]] = field(default_factory=dict)

    @classmethod
    def from_calls(cls, calls: Mapping[str, Iterable[str]]) -> Topology:
        return cls({caller: frozenset(callees) for caller, callees in calls.items()})

    def calls_component(self, caller: str | None, callee: str | None) -> bool:
        if not caller or not callee:
            return False
        return callee in self.calls.get(caller, frozenset())

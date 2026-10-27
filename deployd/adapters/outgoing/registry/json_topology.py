"""Load the declared service topology (the ``calls`` field) from components.json."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from deployd.domain.causal.topology import Topology

if TYPE_CHECKING:
    from pathlib import Path


def load_topology(components_file: Path) -> Topology:
    components = json.loads(components_file.read_text(encoding="utf-8"))
    calls: dict[str, list[str]] = {}
    for name, spec in components.items():
        callees = spec.get("calls", [])
        if not isinstance(callees, list) or not all(isinstance(c, str) for c in callees):
            raise ValueError(f"{components_file}: 'calls' of {name!r} must be a list of names")
        calls[name] = callees
    return Topology.from_calls(calls)

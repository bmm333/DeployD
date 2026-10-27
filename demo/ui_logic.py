"""UI logic without Streamlit: what is about to be sent, and what an investigation left out."""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

REQUIRED_KEYS = ("timestamp", "source", "event_type")
SHOWN_COMPONENTS = 4


def parse_events(raw: str) -> list[dict[str, Any]]:
    """Accept a list of events, {"events": [...]}, or a single event object."""
    data = json.loads(raw)
    if isinstance(data, dict):
        data = data.get("events", [data])
    if not isinstance(data, list) or not all(isinstance(e, dict) for e in data):
        raise ValueError("Expected a JSON list of event objects.")
    if not data:
        raise ValueError("The list has no events.")
    for i, ev in enumerate(data):
        missing = [k for k in REQUIRED_KEYS if k not in ev]
        if missing:
            raise ValueError(f"Event #{i} is missing required field(s): {', '.join(missing)}")
    return data


@dataclass(frozen=True)
class Source:
    """The text that will be sent, where it comes from, and whether a second one is ignored."""

    label: str
    raw: str
    ignored_paste: bool = False


def choose_source(upload_name: str | None, upload_raw: str | None, pasted: str) -> Source | None:
    """An uploaded file wins over pasted text; say so when both are there."""
    pasted_text = pasted.strip()
    if upload_name is not None and upload_raw is not None:
        return Source(upload_name, upload_raw, ignored_paste=bool(pasted_text))
    if pasted_text:
        return Source("pasted text", pasted_text)
    return None


def _distinct(names: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(n for n in names if n and n != "?"))


def describe_events(events: Sequence[Mapping[str, Any]]) -> str:
    """'3 events · analytics-postgres, reporting-service, dashboard-ui'."""
    sources = _distinct(str(e.get("source", "")) for e in events)
    shown = ", ".join(sources[:SHOWN_COMPONENTS])
    if len(sources) > SHOWN_COMPONENTS:
        shown += f" +{len(sources) - SHOWN_COMPONENTS}"
    noun = "event" if len(events) == 1 else "events"
    return f"{len(events)} {noun} · {shown}"


def uncovered_chains(graph: Mapping[str, Any], covered: Iterable[str]) -> list[list[str]]:
    """Causal chains of the graph that share no component with the investigated one.

    A chain is a group of nodes linked by CAUSAL edges; each is returned as its
    components in node order.  ``covered`` are the investigated chain's components.
    """
    nodes = {n["id"]: n for n in graph.get("nodes", [])}
    parent = {node_id: node_id for node_id in nodes}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    linked: set[str] = set()
    for edge in graph.get("edges", []):
        if edge.get("relationship") != "CAUSAL":
            continue
        a, b = edge.get("source"), edge.get("target")
        if a in parent and b in parent:
            parent[find(a)] = find(b)
            linked.update((a, b))

    groups: dict[str, list[str]] = {}
    for node_id in nodes:
        if node_id in linked:
            groups.setdefault(find(node_id), []).append(str(nodes[node_id].get("source") or "?"))

    covered_set = set(covered)
    chains = [_distinct(members) for members in groups.values()]
    return [c for c in chains if c and not covered_set.intersection(c)]

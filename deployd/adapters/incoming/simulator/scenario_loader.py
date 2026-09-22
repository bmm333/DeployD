"""DID-16: ScenarioLoader — loads scenario JSON and builds IncidentGraph + FSM state.

Responsibilities
----------------
1. Parse a scenario JSON file into a ``ScenarioDefinition``.
2. Replay events chronologically through ``ProcessHealthFSM``.
3. Build an ``IncidentGraph`` with explicit edge types:
   - ``CAUSAL`` edge: when ``causal_parent_index`` is declared in the JSON.
   - ``TEMPORAL`` edge: when no parent is declared but a previous event exists
     on the same component. Keeps the graph connected for CausalEngine traversal
     without fabricating causal relationships.
4. Resolve the retrieval query deterministically (explicit field → first CRITICAL
   event description → scenario description fallback).

What it does NOT do
-------------------
- It does not run retrieval.
- It does not call the orchestrator.
- It does not touch the AI agent.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

from deployd.domain.entities.core_event import CoreEvent, CoreEventType, Severity
from deployd.domain.graph.edge import GraphEdge
from deployd.domain.graph.edge_type import EdgeType
from deployd.domain.graph.graph import IncidentGraph
from deployd.domain.graph.node import GraphNode
from deployd.domain.health.process_health import ProcessHealthFSM, Transition

if TYPE_CHECKING:
    import uuid
    from pathlib import Path

    from deployd.domain.health.process_state import ProcessHealthStatus


@dataclass(frozen=True)
class ScenarioDefinition:
    """Parsed representation of a scenario JSON file."""

    scenario_id: str
    description: str
    component: str
    expected_tier: str
    retrieval_query: str | None
    events: list[CoreEvent]


@dataclass
class ScenarioGraphResult:
    """Output of ScenarioLoader.build_graph()."""

    graph: IncidentGraph
    fsm_state: ProcessHealthStatus
    transitions: list[Transition]
    node_ids: list[uuid.UUID]  # ordered list of node IDs, one per event


class ScenarioLoader:
    """Loads a scenario JSON file and builds the investigation inputs.

    Parameters
    ----------
    fsm_recovery_window_s:
        ProcessHealthFSM recovery window in seconds. Default 300 (5 minutes).
    fsm_max_restarts:
        Maximum restart count before CRASH_LOOP. Default 3.
    fsm_restart_window_s:
        Restart time window in seconds. Default 120 (2 minutes).
    """

    def __init__(
        self,
        fsm_recovery_window_s: int = 300,
        fsm_max_restarts: int = 3,
        fsm_restart_window_s: int = 120,
    ) -> None:
        self._fsm_recovery_window_s = fsm_recovery_window_s
        self._fsm_max_restarts = fsm_max_restarts
        self._fsm_restart_window_s = fsm_restart_window_s

    def load(self, path: Path) -> ScenarioDefinition:
        """Parse a scenario JSON file into a ScenarioDefinition.

        Timestamps are resolved by applying each event's
        ``timestamp_offset_seconds`` to the current UTC time at load time.
        This avoids stale absolute timestamps in the JSON files.
        """
        raw: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        base_time = datetime.now(timezone.utc)

        raw_events: list[dict[str, Any]] = raw.get("events", [])
        events: list[CoreEvent] = []
        for i, evt in enumerate(raw_events):
            parent_idx = evt.get("causal_parent_index")
            if parent_idx is not None and parent_idx >= i:
                raise ValueError(
                    f"Event {i} has causal_parent_index={parent_idx} "
                    f"which is >= its own index — parent must come before child."
                )

            offset = timedelta(seconds=float(evt.get("timestamp_offset_seconds", 0)))
            events.append(
                CoreEvent(
                    event_type=CoreEventType(evt["event_type"]),
                    timestamp=base_time + offset,
                    severity=Severity(evt["severity"]),
                    related_component=raw.get("component"),
                    description=evt["description"],
                    metadata=evt.get("metadata", {}),
                )
            )

        return ScenarioDefinition(
            scenario_id=raw["scenario_id"],
            description=raw["description"],
            component=raw["component"],
            expected_tier=raw["expected_tier"],
            retrieval_query=raw.get("retrieval_query"),
            events=events,
        )

    def build_graph(
        self,
        definition: ScenarioDefinition,
        raw_events_meta: list[dict[str, Any]] | None = None,
    ) -> ScenarioGraphResult:
        """Build IncidentGraph and replay events through the FSM.

        Parameters
        ----------
        definition:
            Parsed scenario. Events are replayed in order.
        raw_events_meta:
            Optional list of raw event dicts from the JSON (used to read
            ``causal_parent_index``). If None, no CAUSAL edges are added
            and all inter-event edges are TEMPORAL.
        """
        fsm = ProcessHealthFSM(
            recovery_window=timedelta(seconds=self._fsm_recovery_window_s),
            max_restart_count=self._fsm_max_restarts,
            restart_time_window=timedelta(seconds=self._fsm_restart_window_s),
        )
        graph = IncidentGraph()
        node_ids: list[uuid.UUID] = []

        for i, event in enumerate(definition.events):
            node = GraphNode(event=event)
            graph.add_node(node)
            node_ids.append(node.node_id)
            fsm.process_event(event)

            if i == 0:
                continue

            # Determine edge type from raw metadata
            causal_parent_idx: int | None = None
            if raw_events_meta is not None and i < len(raw_events_meta):
                raw_idx = raw_events_meta[i].get("causal_parent_index")
                if raw_idx is not None:
                    causal_parent_idx = int(raw_idx)

            if causal_parent_idx is not None and 0 <= causal_parent_idx < i:
                parent_node_id = node_ids[causal_parent_idx]
                edge_type = EdgeType.CAUSAL
                confidence = 1.0
                rule_id = "scenario:causal_link"
            else:
                # TEMPORAL edge to the immediately preceding event — keeps
                # the graph connected for CausalEngine traversal without
                # fabricating causal meaning we have not declared.
                parent_node_id = node_ids[i - 1]
                edge_type = EdgeType.TEMPORAL
                confidence = 0.5
                rule_id = "scenario:temporal_sequence"

            graph.add_edge(
                GraphEdge(
                    source=parent_node_id,
                    target=node.node_id,
                    edge_type=edge_type,
                    confidence=confidence,
                    rule_id=rule_id,
                )
            )

        return ScenarioGraphResult(
            graph=graph,
            fsm_state=fsm.state,
            transitions=fsm.transition_history,
            node_ids=node_ids,
        )

    def resolve_query(self, definition: ScenarioDefinition) -> str:
        """Resolve the retrieval query for a scenario.

        Priority:
        1. Explicit ``retrieval_query`` field in the JSON.
        2. Scenario ``description`` field.
        3. Fallback: component + first CRITICAL event type.
        """
        if definition.retrieval_query:
            return definition.retrieval_query
        if definition.description:
            return definition.description
        for event in definition.events:
            if event.severity == Severity.CRITICAL:
                return f"{definition.component}: {event.event_type.value}"
        return f"{definition.component}: unknown failure"

    def load_with_meta(self, path: Path) -> tuple[ScenarioDefinition, list[dict[str, Any]]]:
        """Load a scenario and return both the definition and raw event dicts.

        The raw event dicts carry ``causal_parent_index`` which is not part
        of ``CoreEvent`` (it is a loader artefact, not a domain concept).
        """
        raw: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        definition = self.load(path)
        return definition, raw.get("events", [])

"""JsonInvestigationRepository — file-system JSON adapter.

Each Investigation is stored as a separate JSON file:
    <storage_dir>/<investigation_id>.json

Serialisation strategy:
  - ``Investigation`` fields are dumped via ``model_dump(mode="json")``
    (Pydantic handles UUID → str, datetime → ISO-8601, enum → str).
  - ``IncidentGraph`` is not a Pydantic model, so it is encoded separately
    by ``_encode_graph()`` and reconstructed by ``_decode_graph()``.
  - All round-trips are tested in ``tests/unit/adapters/test_json_repo.py``.

Safety boundary:
  This adapter only reads and writes JSON files.  It has no connection to
  production infrastructure and contains no remediation logic.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

from deployd.domain.entities.core_event import CoreEvent
from deployd.domain.entities.investigation import Investigation
from deployd.domain.graph.edge import GraphEdge
from deployd.domain.graph.edge_type import EdgeType
from deployd.domain.graph.graph import IncidentGraph
from deployd.domain.graph.node import GraphNode
from deployd.domain.repositories.investigation_repo import InvestigationNotFoundError

# ---------------------------------------------------------------------------
# Graph codec helpers
# ---------------------------------------------------------------------------


def _encode_graph(graph: IncidentGraph) -> dict[str, Any]:
    """Serialise IncidentGraph to a JSON-compatible dict."""
    nodes = []
    for node in graph.nodes:
        nodes.append(node.event.model_dump(mode="json"))

    edges = []
    for edge in graph.edges:
        edges.append(edge.model_dump(mode="json"))

    return {"nodes": nodes, "edges": edges}


def _decode_graph(data: dict[str, Any]) -> IncidentGraph:
    """Reconstruct an IncidentGraph from a serialised dict."""
    graph = IncidentGraph()
    for raw_node in data.get("nodes", []):
        event = CoreEvent.model_validate(raw_node)
        graph.add_node(GraphNode(event=event))

    for raw_edge in data.get("edges", []):
        # edge_type comes back as str, convert to enum
        raw_edge["edge_type"] = EdgeType(raw_edge["edge_type"])
        # UUIDs come back as str
        raw_edge["source"] = uuid.UUID(raw_edge["source"])
        raw_edge["target"] = uuid.UUID(raw_edge["target"])
        graph.add_edge(GraphEdge(**raw_edge))

    return graph


# ---------------------------------------------------------------------------
# Serialisation helpers
# ---------------------------------------------------------------------------


def _investigation_to_dict(investigation: Investigation) -> dict[str, Any]:
    """Dump an Investigation to a JSON-serialisable dict."""
    data = investigation.model_dump(mode="json", exclude={"incident_graph"})
    data["incident_graph"] = (
        _encode_graph(investigation.incident_graph)
        if investigation.incident_graph is not None
        else None
    )
    return data


def _investigation_from_dict(data: dict[str, Any]) -> Investigation:
    """Reconstruct an Investigation from a deserialised dict."""
    raw_graph = data.pop("incident_graph", None)
    investigation = Investigation.model_validate(data)
    if raw_graph is not None:
        investigation.incident_graph = _decode_graph(raw_graph)
    return investigation


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------


class JsonInvestigationRepository:
    """Stores investigations as individual JSON files in *storage_dir*.

    Satisfies ``InvestigationRepository`` Protocol structurally.
    """

    def __init__(self, storage_dir: str | Path) -> None:
        self._dir = Path(storage_dir)
        self._dir.mkdir(parents=True, exist_ok=True)

    def _path(self, investigation_id: uuid.UUID) -> Path:
        return self._dir / f"{investigation_id}.json"

    # ------------------------------------------------------------------
    # Protocol methods
    # ------------------------------------------------------------------

    def save(self, investigation: Investigation) -> None:
        """Persist a new Investigation as a JSON file."""
        path = self._path(investigation.investigation_id)
        data = _investigation_to_dict(investigation)
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def get(self, investigation_id: uuid.UUID) -> Investigation:
        """Load an Investigation by its ID.

        Raises:
            InvestigationNotFoundError: if no file exists for this ID.
        """
        path = self._path(investigation_id)
        if not path.exists():
            raise InvestigationNotFoundError(investigation_id)
        raw = json.loads(path.read_text(encoding="utf-8"))
        return _investigation_from_dict(raw)

    def update(self, investigation: Investigation) -> None:
        """Overwrite the persisted JSON for an existing Investigation.

        Raises:
            InvestigationNotFoundError: if no file exists for this ID.
        """
        path = self._path(investigation.investigation_id)
        if not path.exists():
            raise InvestigationNotFoundError(investigation.investigation_id)
        data = _investigation_to_dict(investigation)
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def list(self) -> list[Investigation]:
        """Return all persisted Investigations in undefined order."""
        investigations = []
        for json_file in self._dir.glob("*.json"):
            raw = json.loads(json_file.read_text(encoding="utf-8"))
            investigations.append(_investigation_from_dict(raw))
        return investigations

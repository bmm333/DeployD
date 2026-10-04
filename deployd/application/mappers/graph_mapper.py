"""
ADR-008
GraphMapper — converts an ``IncidentGraph`` into DTO-level dependency edges.

The mapper reads ``GraphEdge`` objects (which carry domain ``EdgeType`` enums)
and translates them into ``ComponentDependencyDTO`` instances using plain
strings. The ``IncidentGraph`` itself never crosses the agent boundary.

Rules (ADR-008):
- Only ``ComponentDependencyDTO`` objects may leave this mapper.
- ``EdgeType`` enum values are flattened to their string representations.
- Node labels are the ``related_component`` field of the underlying ``CoreEvent``.
  If a node has no component name, it falls back to the string form of its UUID.
"""

from __future__ import annotations

from deployd.application.dtos.investigation_request import ComponentDependencyDTO
from deployd.domain.graph.graph import IncidentGraph


class GraphMapper:
    """
    Converts an ``IncidentGraph`` into a flat list of ``ComponentDependencyDTO``.

    Instantiate once and reuse — the mapper is stateless.
    """

    @staticmethod
    def graph_to_dependency_map(graph: IncidentGraph) -> list[ComponentDependencyDTO]:
        """
        Translate every edge in the ``IncidentGraph`` into a ``ComponentDependencyDTO``.

        Edges whose source or target node lacks a ``related_component`` will
        use the UUID string as a fallback label so that the resulting DTO list
        is always complete.

        Duplicate (source, target, relationship) triplets are deduplicated before
        returning — the agent does not need redundant edges.
        """
        deps: list[ComponentDependencyDTO] = []
        seen: set[tuple[str, str, str]] = set()

        for edge in graph.edges:
            source_node = graph.get_node(edge.source)
            target_node = graph.get_node(edge.target)

            source_label = source_node.event.related_component or str(edge.source)
            target_label = target_node.event.related_component or str(edge.target)
            relationship = edge.edge_type.value  # EdgeType → plain str

            key = (source_label, target_label, relationship)
            if key in seen:
                continue
            seen.add(key)

            deps.append(
                ComponentDependencyDTO(
                    source_component=source_label,
                    target_component=target_label,
                    relationship=relationship,
                )
            )

        return deps

    @staticmethod
    def affected_components(graph: IncidentGraph) -> list[str]:
        """
        Return a deduplicated, ordered list of component names from all graph nodes.

        Used to populate ``InvestigationRequest.affected_components`` and
        ``IncidentSummaryDTO.affected_components``.
        """
        seen: set[str] = set()
        result: list[str] = []
        for node in graph.nodes:
            label = node.event.related_component or str(node.node_id)
            if label not in seen:
                seen.add(label)
                result.append(label)
        return result

from dataclasses import dataclass


@dataclass(frozen=True)
class RunbookStructure:
    runbook_id: str
    causal_chain: tuple[str, ...]
    affected_components: frozenset[str]


class GraphStore:
    """Inmem store of runbookstruct"""

    def __init__(self) -> None:
        self._structures: dict[str, RunbookStructure] = {}

    def add(self, structure: RunbookStructure) -> None:
        self._structures[structure.runbook_id] = structure

    def all(self) -> list[RunbookStructure]:
        return list(self._structures.values())

    def get(self, runbook_id: str) -> RunbookStructure | None:
        return self._structures.get(runbook_id)

"""JSONRunbookRepository - loads historical runbooks from JSON files."""

import json
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class RunbookDetail:
    runbook_id: str
    incident_id: str
    summary: str
    root_cause: str
    fix: str
    fix_commands: list[str] = field(default_factory=list)
    causal_chain: list[str] = field(default_factory=list)
    affected_components: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)


class JSONRunbookRepository:
    """Loads and queries historical runbook details from data/runbooks directory."""

    def __init__(self, runbooks_dir: str | Path | None = None) -> None:
        if runbooks_dir is None:
            root = Path(__file__).parent.parent.parent.parent.parent
            runbooks_dir = root / "data" / "runbooks"
        self._dir = Path(runbooks_dir)
        self._cache: dict[str, RunbookDetail] = {}
        self._load()

    def _load(self) -> None:
        if not self._dir.exists():
            return
        for file in sorted(self._dir.glob("rb_*.json")):
            with open(file, encoding="utf-8") as f:
                data = json.load(f)
                detail = RunbookDetail(
                    runbook_id=data["runbook_id"],
                    incident_id=data.get("incident_id", ""),
                    summary=data.get("summary", ""),
                    root_cause=data.get("root_cause", ""),
                    fix=data.get("fix", ""),
                    fix_commands=data.get("fix_commands", []),
                    causal_chain=data.get("causal_chain", []),
                    affected_components=data.get("affected_components", []),
                    tags=data.get("tags", []),
                )
                self._cache[detail.runbook_id] = detail

    def get_by_id(self, runbook_id: str) -> RunbookDetail | None:
        return self._cache.get(runbook_id)

    def list_all(self) -> list[RunbookDetail]:
        return list(self._cache.values())

"""SQLite-backed IncidentRepository."""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

from deployd.domain.entities.core_event import Severity
from deployd.domain.incident.incident import Incident

_SCHEMA = """
CREATE TABLE IF NOT EXISTS incidents (
    id              TEXT PRIMARY KEY,
    opened_at       TEXT NOT NULL,
    resolved_at     TEXT,
    status          TEXT NOT NULL DEFAULT 'OPEN',
    peak_severity   TEXT NOT NULL DEFAULT 'INFO',
    graph_snapshot  TEXT NOT NULL DEFAULT '[]',
    chat_history    TEXT NOT NULL DEFAULT '[]',
    root_cause_summary TEXT
);
"""


class SQLiteIncidentRepository:
    """Persists incidents to a local SQLite database."""

    def __init__(self, db_path: str | Path = "data/deployd.db") -> None:
        self._path = Path(db_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._path))
        conn.row_factory = sqlite3.Row
        return conn

    def save(self, incident: Incident) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO incidents
                    (id, opened_at, resolved_at, status, peak_severity,
                     graph_snapshot, chat_history, root_cause_summary)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    resolved_at        = excluded.resolved_at,
                    status             = excluded.status,
                    peak_severity      = excluded.peak_severity,
                    graph_snapshot     = excluded.graph_snapshot,
                    chat_history       = excluded.chat_history,
                    root_cause_summary = excluded.root_cause_summary
                """,
                (
                    str(incident.id),
                    incident.opened_at.isoformat(),
                    incident.resolved_at.isoformat() if incident.resolved_at else None,
                    incident.status,
                    incident.peak_severity.value,
                    json.dumps(incident.graph_snapshot),
                    json.dumps(incident.chat_history),
                    incident.root_cause_summary,
                ),
            )

    def get(self, incident_id: uuid.UUID) -> Incident | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM incidents WHERE id = ?", (str(incident_id),)
            ).fetchone()
        return self._row_to_incident(row) if row else None

    def list_all(self) -> list[Incident]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM incidents ORDER BY opened_at DESC").fetchall()
        return [self._row_to_incident(r) for r in rows]

    def get_current(self) -> Incident | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM incidents WHERE status = 'OPEN' ORDER BY opened_at DESC LIMIT 1"
            ).fetchone()
        return self._row_to_incident(row) if row else None

    @staticmethod
    def _row_to_incident(row: sqlite3.Row) -> Incident:
        def _dt(val: str | None) -> datetime | None:
            if val is None:
                return None
            dt = datetime.fromisoformat(val)
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)

        opened = _dt(row["opened_at"])
        assert opened is not None
        return Incident(
            id=uuid.UUID(row["id"]),
            opened_at=opened,
            resolved_at=_dt(row["resolved_at"]),
            status=row["status"],
            peak_severity=Severity(row["peak_severity"]),
            graph_snapshot=json.loads(row["graph_snapshot"]),
            chat_history=json.loads(row["chat_history"]),
            root_cause_summary=row["root_cause_summary"],
        )

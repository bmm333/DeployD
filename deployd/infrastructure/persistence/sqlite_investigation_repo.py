"""SqliteInvestigationRepository — SQLite adapter (stdlib sqlite3, no ORM).

Schema:
    CREATE TABLE investigations (
        id   TEXT PRIMARY KEY,
        data TEXT NOT NULL      -- JSON blob
    )

Serialisation is identical to the JSON adapter (same helpers reused).
Tests use an in-memory database (``":memory:"``).

Safety boundary:
  This adapter only reads and writes a local SQLite database.  It has no
  connection to production infrastructure and contains no remediation logic.
"""

from __future__ import annotations

import json
import sqlite3
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import uuid

    from deployd.domain.entities.investigation import Investigation
from deployd.domain.repositories.investigation_repo import InvestigationNotFoundError
from deployd.infrastructure.persistence.json_investigation_repo import (
    _investigation_from_dict,
    _investigation_to_dict,
)

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS investigations (
    id   TEXT PRIMARY KEY,
    data TEXT NOT NULL
)
"""


class SqliteInvestigationRepository:
    """Stores investigations as JSON blobs in a SQLite table.

    Satisfies ``InvestigationRepository`` Protocol structurally.

    Args:
        db_path: Path to the SQLite database file, or ``":memory:"`` for an
            in-memory database (useful for testing and CI).
    """

    def __init__(self, db_path: str = ":memory:") -> None:
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.execute(_CREATE_TABLE)
        self._conn.commit()

    # ------------------------------------------------------------------
    # Protocol methods
    # ------------------------------------------------------------------

    def save(self, investigation: Investigation) -> None:
        """Persist a new Investigation as a JSON blob."""
        data = _investigation_to_dict(investigation)
        self._conn.execute(
            "INSERT INTO investigations (id, data) VALUES (?, ?)",
            (str(investigation.investigation_id), json.dumps(data)),
        )
        self._conn.commit()

    def get(self, investigation_id: uuid.UUID) -> Investigation:
        """Load an Investigation by its ID.

        Raises:
            InvestigationNotFoundError: if no row exists for this ID.
        """
        cursor = self._conn.execute(
            "SELECT data FROM investigations WHERE id = ?",
            (str(investigation_id),),
        )
        row = cursor.fetchone()
        if row is None:
            raise InvestigationNotFoundError(investigation_id)
        raw: dict[str, Any] = json.loads(row[0])
        return _investigation_from_dict(raw)

    def update(self, investigation: Investigation) -> None:
        """Overwrite the JSON blob for an existing Investigation.

        Raises:
            InvestigationNotFoundError: if no row exists for this ID.
        """
        # Verify existence first to give a domain-level error.
        cursor = self._conn.execute(
            "SELECT 1 FROM investigations WHERE id = ?",
            (str(investigation.investigation_id),),
        )
        if cursor.fetchone() is None:
            raise InvestigationNotFoundError(investigation.investigation_id)

        data = _investigation_to_dict(investigation)
        self._conn.execute(
            "UPDATE investigations SET data = ? WHERE id = ?",
            (json.dumps(data), str(investigation.investigation_id)),
        )
        self._conn.commit()

    def list(self) -> list[Investigation]:
        """Return all persisted Investigations in undefined order."""
        cursor = self._conn.execute("SELECT data FROM investigations")
        rows = cursor.fetchall()
        return [_investigation_from_dict(json.loads(row[0])) for row in rows]

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def close(self) -> None:
        """Close the underlying database connection."""
        self._conn.close()

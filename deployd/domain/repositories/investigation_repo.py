"""
InvestigationRepository — domain port (Protocol).

The application layer depends on this interface.  Concrete adapters
(JSON, SQLite, …) live in infrastructure/persistence/ and satisfy this
Protocol structurally — they do NOT import from this module to do so.

Using typing.Protocol (structural subtyping) keeps the domain layer
dependency-free: adapters only need to implement the right method signatures.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    import uuid

from deployd.domain.entities.investigation import Investigation  # noqa: TCH001

# ---------------------------------------------------------------------------
# Domain exception
# ---------------------------------------------------------------------------


class InvestigationNotFoundError(Exception):
    """Raised when get() is called with an ID that has no persisted record."""

    def __init__(self, investigation_id: uuid.UUID) -> None:
        super().__init__(f"Investigation {investigation_id} not found")
        self.investigation_id = investigation_id


# ---------------------------------------------------------------------------
# Port
# ---------------------------------------------------------------------------


@runtime_checkable
class InvestigationRepository(Protocol):
    """Abstract persistence port for Investigation aggregates.

    All operations are synchronous.  If async persistence is needed in
    future, a separate AsyncInvestigationRepository Protocol should be
    defined rather than modifying this one.
    """

    def save(self, investigation: Investigation) -> None:
        """Persist a new Investigation.

        Args:
            investigation: the aggregate to store.

        Raises:
            Any adapter-specific error if persistence fails.
        """
        ...

    def get(self, investigation_id: uuid.UUID) -> Investigation:
        """Retrieve an Investigation by its ID.

        Args:
            investigation_id: the UUID of the investigation.

        Returns:
            The persisted Investigation aggregate.

        Raises:
            InvestigationNotFoundError: if no record exists for this ID.
        """
        ...

    def update(self, investigation: Investigation) -> None:
        """Overwrite the persisted state of an existing Investigation.

        Args:
            investigation: the aggregate with updated state.

        Raises:
            InvestigationNotFoundError: if no record exists for this ID.
        """
        ...

    def list(self) -> list[Investigation]:
        """Return all persisted Investigations.

        Returns:
            A list of Investigation aggregates, possibly empty.
        """
        ...

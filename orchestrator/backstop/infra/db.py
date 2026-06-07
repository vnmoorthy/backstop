"""Async SQLite (WAL) engine + session factory (stub).

Owns the ``aiosqlite`` connection lifecycle behind ``AppealRepositoryPort`` /
``AuditLogPort`` / ``CostLedgerPort``. WAL mode, single writer, atomic
transactions, row caps and TTL eviction live here; ``DATABASE_URL`` allows a
Postgres swap later without touching any service.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - typing only
    from backstop.infra.config import Settings


class Database:
    """Lifecycle-managed async SQLite engine handle.

    A thin owner of the underlying ``aiosqlite`` connection/pool. Real
    initialisation (WAL pragma, table creation) is filled in by WS-Integration.
    """

    def __init__(self, settings: Settings) -> None:
        """Store settings; defer connection until :meth:`connect`.

        Args:
            settings: The frozen application settings (provides ``database_url``).
        """
        self._settings = settings

    async def connect(self) -> None:
        """Open the connection, set WAL pragma and create tables idempotently.

        Raises:
            NotImplementedError: Always — filled in by WS-Integration.
        """
        raise NotImplementedError("Database.connect is implemented in WS-Integration")

    async def session(self) -> Any:
        """Yield/return a single-writer transactional session.

        Returns:
            A session/connection object scoped to one transaction.

        Raises:
            NotImplementedError: Always — filled in by WS-Integration.
        """
        raise NotImplementedError("Database.session is implemented in WS-Integration")

    async def aclose(self) -> None:
        """Close the engine on application shutdown.

        Raises:
            NotImplementedError: Always — filled in by WS-Integration.
        """
        raise NotImplementedError("Database.aclose is implemented in WS-Integration")


def make_db(settings: Settings) -> Database:
    """Construct the async SQLite :class:`Database` handle.

    Args:
        settings: The frozen application settings.

    Returns:
        An unconnected :class:`Database`; call :meth:`Database.connect` in
        the application lifespan.
    """
    return Database(settings)

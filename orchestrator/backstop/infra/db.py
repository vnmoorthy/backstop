"""Async SQLite (WAL) path resolution + lifecycle helper.

The concrete ``aiosqlite`` connection lifecycle lives on
``SqliteAppealRepo`` / ``HashChainAuditAdapter`` (each owns its own connection,
opened in :meth:`connect` and closed in :meth:`aclose`). This module owns only
the one impure-but-trivial concern shared across those adapters: turning the
configured ``DATABASE_URL`` into a concrete on-disk path (or ``":memory:"``).
``DATABASE_URL`` allows a Postgres swap later without touching any service.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - typing only
    from backstop.infra.config import Settings

# Recognised SQLite URL schemes; the path is everything after the scheme's
# ``///`` (an absolute or relative file path) or ``:memory:``.
_SQLITE_SCHEMES = ("sqlite+aiosqlite://", "sqlite://")


def sqlite_path_from_url(database_url: str) -> str:
    """Resolve a ``DATABASE_URL`` to a concrete SQLite path.

    ``sqlite+aiosqlite:///./backstop.db`` -> ``./backstop.db``;
    ``sqlite:///:memory:`` -> ``:memory:``. A non-SQLite URL is returned
    unchanged so the caller can fail fast on an unsupported backend.

    Args:
        database_url: The configured database URL.

    Returns:
        The bare SQLite file path (or ``":memory:"``).
    """
    for scheme in _SQLITE_SCHEMES:
        if database_url.startswith(scheme):
            remainder = database_url[len(scheme) :]
            # ``sqlite:///path`` -> the leading slash of the triple is consumed
            # by the scheme's ``//``; strip exactly one more to get ``/path`` or
            # a relative ``path``.
            return remainder[1:] if remainder.startswith("/") else remainder
    return database_url


class Database:
    """Resolves the SQLite path for the persistence adapters.

    A thin owner that does not itself hold a connection — each repository/audit
    adapter opens its own ``aiosqlite`` connection against
    :attr:`sqlite_path`. The lifespan connects/closes those adapters; this
    handle exists so the composition root has one place that interprets
    ``DATABASE_URL``.
    """

    def __init__(self, settings: Settings) -> None:
        """Resolve and store the SQLite path from ``settings.database_url``."""
        self._settings = settings
        self.sqlite_path = sqlite_path_from_url(settings.database_url)


def make_db(settings: Settings) -> Database:
    """Construct the :class:`Database` path handle.

    Args:
        settings: The frozen application settings.

    Returns:
        A :class:`Database` exposing the resolved SQLite path.
    """
    return Database(settings)

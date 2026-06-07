"""Durable SQLite appeal repository for :class:`AppealRepositoryPort`.

Implements :class:`backstop.ports.appeal_repository_port.AppealRepositoryPort`
over WAL-mode SQLite. Two behaviours are load-bearing for correctness and
security:

* **Compare-and-swap status** -- :meth:`update_status_atomic` is a single
  ``UPDATE ... WHERE status = :expected`` inside an ``IMMEDIATE`` transaction,
  bumping ``version`` in the same statement. The database rowcount decides the
  winner, so two concurrent writers racing the same transition cannot both
  succeed -- this is the snapshot-watchdog torn-read race fix. A stale expected
  status returns ``False`` rather than clobbering a newer write.
* **Append-only events** -- :meth:`append_event` inserts into an events table with
  ``UNIQUE(appeal_id, seq)``; it never updates or deletes a prior row, and a
  duplicate ``seq`` is rejected.

``aiosqlite`` is imported lazily inside :meth:`connect` so this module imports
cleanly even when the SDK is absent. A single :class:`asyncio.Lock` serialises
writes (SQLite is single-writer) while reads remain concurrent under WAL.
"""

from __future__ import annotations

import asyncio
import dataclasses
import sqlite3
from typing import TYPE_CHECKING, Any, List, Optional

from backstop.adapters.persistence._appeal_codec import decode_appeal, encode_appeal
from backstop.domain.enums import AppealStatus
from backstop.domain.errors import AppealNotFound
from backstop.domain.models import Appeal
from backstop.ports.appeal_repository_port import (
    AppealEventRecord,
    AppealFilter,
    AppealPage,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    import aiosqlite

__all__ = ["SqliteAppealRepo"]

# Hard ceiling on a single ``list`` page so a caller cannot request an unbounded
# slice (mirrors the memory repo's clamp).
_MAX_LIMIT = 500

_CREATE_APPEALS = """
CREATE TABLE IF NOT EXISTS appeals (
    id                 TEXT PRIMARY KEY,
    status             TEXT NOT NULL,
    payer_id           TEXT NOT NULL,
    needs_human_review INTEGER NOT NULL,
    version            INTEGER NOT NULL DEFAULT 0,
    blob               TEXT NOT NULL
)
"""

_CREATE_EVENTS = """
CREATE TABLE IF NOT EXISTS appeal_events (
    appeal_id       TEXT NOT NULL,
    seq             INTEGER NOT NULL,
    kind            TEXT NOT NULL,
    ts_iso          TEXT NOT NULL,
    payload_json    TEXT NOT NULL,
    provenance_json TEXT,
    PRIMARY KEY (appeal_id, seq),
    FOREIGN KEY (appeal_id) REFERENCES appeals (id)
)
"""


class SqliteAppealRepo:
    """WAL SQLite :class:`~backstop.ports.appeal_repository_port.AppealRepositoryPort`.

    Construct with a ``database_path`` (``":memory:"`` is supported for tests),
    then ``await connect()`` once before use and ``await aclose()`` on shutdown.
    """

    def __init__(self, *, database_path: str) -> None:
        """Store the path; defer the connection until :meth:`connect`."""
        self._database_path = database_path
        self._conn: Optional[aiosqlite.Connection] = None
        self._write_lock = asyncio.Lock()

    async def connect(self) -> None:
        """Open the connection, set WAL pragma and create tables idempotently."""
        import aiosqlite

        conn = await aiosqlite.connect(self._database_path)
        conn.row_factory = aiosqlite.Row
        await conn.execute("PRAGMA journal_mode=WAL")
        await conn.execute("PRAGMA foreign_keys=ON")
        await conn.execute(_CREATE_APPEALS)
        await conn.execute(_CREATE_EVENTS)
        await conn.commit()
        self._conn = conn

    async def aclose(self) -> None:
        """Close the underlying connection (idempotent)."""
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    async def save(self, appeal: Appeal) -> None:
        """Insert or upsert ``appeal`` (full aggregate + indexed columns)."""
        conn = self._require_conn()
        blob = encode_appeal(appeal)
        async with self._write_lock:
            await conn.execute(
                """
                INSERT INTO appeals (id, status, payer_id, needs_human_review, version, blob)
                VALUES (:id, :status, :payer_id, :needs, 0, :blob)
                ON CONFLICT(id) DO UPDATE SET
                    status = excluded.status,
                    payer_id = excluded.payer_id,
                    needs_human_review = excluded.needs_human_review,
                    blob = excluded.blob,
                    version = appeals.version + 1
                """,
                {
                    "id": appeal.id,
                    "status": appeal.status.value,
                    "payer_id": appeal.denial.payer.payer_id,
                    "needs": 1 if appeal.needs_human_review else 0,
                    "blob": blob,
                },
            )
            await conn.commit()

    async def load(self, appeal_id: str) -> Appeal:
        """Return the appeal with ``appeal_id`` or raise :class:`AppealNotFound`."""
        conn = self._require_conn()
        async with conn.execute(
            "SELECT blob FROM appeals WHERE id = ?", (appeal_id,)
        ) as cursor:
            row = await cursor.fetchone()
        if row is None:
            raise AppealNotFound(appeal_id)
        return decode_appeal(row["blob"])

    async def list(
        self,
        filter: AppealFilter,
        *,
        limit: int,
        offset: int,
    ) -> AppealPage:
        """Return a bounded, filtered page of appeals."""
        conn = self._require_conn()
        clamped_limit = max(0, min(limit, _MAX_LIMIT))
        clamped_offset = max(0, offset)

        where: List[str] = []
        params: List[Any] = []
        if filter.status is not None:
            where.append("status = ?")
            params.append(filter.status.value)
        if filter.payer_id is not None:
            where.append("payer_id = ?")
            params.append(filter.payer_id)
        if filter.needs_human_review is not None:
            where.append("needs_human_review = ?")
            params.append(1 if filter.needs_human_review else 0)
        clause = (" WHERE " + " AND ".join(where)) if where else ""

        async with conn.execute(
            f"SELECT COUNT(*) AS n FROM appeals{clause}", params  # noqa: S608
        ) as cursor:
            count_row = await cursor.fetchone()
        total = int(count_row["n"]) if count_row is not None else 0

        async with conn.execute(
            f"SELECT blob FROM appeals{clause} ORDER BY id LIMIT ? OFFSET ?",  # noqa: S608
            (*params, clamped_limit, clamped_offset),
        ) as cursor:
            rows = await cursor.fetchall()

        items = [decode_appeal(row["blob"]) for row in rows]
        return AppealPage(
            items=items,
            total=total,
            limit=clamped_limit,
            offset=clamped_offset,
        )

    async def update_status_atomic(
        self,
        appeal_id: str,
        expected: AppealStatus,
        new: AppealStatus,
    ) -> bool:
        """Compare-and-swap the status in one transaction; bump ``version``.

        Returns ``True`` only when the row's persisted status equalled
        ``expected`` at update time; ``False`` on a stale read. The blob's status
        field is rewritten in step so reads stay consistent.
        """
        conn = self._require_conn()
        async with self._write_lock:
            await conn.execute("BEGIN IMMEDIATE")
            try:
                async with conn.execute(
                    "SELECT blob FROM appeals WHERE id = ?", (appeal_id,)
                ) as cursor:
                    row = await cursor.fetchone()
                if row is None:
                    await conn.rollback()
                    raise AppealNotFound(appeal_id)

                cursor = await conn.execute(
                    """
                    UPDATE appeals
                       SET status = :new, version = version + 1
                     WHERE id = :id AND status = :expected
                    """,
                    {"id": appeal_id, "new": new.value, "expected": expected.value},
                )
                if cursor.rowcount != 1:
                    await conn.rollback()
                    return False

                appeal = decode_appeal(row["blob"])
                updated = dataclasses.replace(appeal, status=new)
                await conn.execute(
                    "UPDATE appeals SET blob = :blob WHERE id = :id",
                    {"blob": encode_appeal(updated), "id": appeal_id},
                )
                await conn.commit()
                return True
            except BaseException:
                await conn.rollback()
                raise

    async def append_event(
        self,
        appeal_id: str,
        event: AppealEventRecord,
    ) -> None:
        """Append one timeline event; rejects a duplicate ``(appeal_id, seq)``."""
        conn = self._require_conn()
        async with self._write_lock:
            async with conn.execute(
                "SELECT 1 FROM appeals WHERE id = ?", (appeal_id,)
            ) as cursor:
                if await cursor.fetchone() is None:
                    raise AppealNotFound(appeal_id)
            try:
                await conn.execute(
                    """
                    INSERT INTO appeal_events
                        (appeal_id, seq, kind, ts_iso, payload_json, provenance_json)
                    VALUES (:appeal_id, :seq, :kind, :ts_iso, :payload, :provenance)
                    """,
                    {
                        "appeal_id": appeal_id,
                        "seq": event.seq,
                        "kind": event.kind,
                        "ts_iso": event.ts_iso,
                        "payload": event.payload_json,
                        "provenance": event.provenance_json,
                    },
                )
            except sqlite3.IntegrityError as exc:
                await conn.rollback()
                raise ValueError(
                    f"duplicate event seq {event.seq} for appeal {appeal_id}"
                ) from exc
            await conn.commit()

    def _require_conn(self) -> aiosqlite.Connection:
        """Return the open connection or raise if :meth:`connect` was skipped."""
        if self._conn is None:
            raise RuntimeError("SqliteAppealRepo.connect() must be awaited before use")
        return self._conn

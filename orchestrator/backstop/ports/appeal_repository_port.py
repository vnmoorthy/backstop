"""Appeal repository port: persistence boundary for the ``Appeal`` aggregate.

Defines the :class:`AppealRepositoryPort` protocol plus its request/result DTOs.
The repository is the single seam through which services load, persist, list,
and atomically transition appeals. Two behaviours are load-bearing for security:

* ``update_status_atomic`` performs an optimistic-lock compare-and-swap (CAS)
  on the aggregate ``version``, eliminating the snapshot-watchdog torn-read race.
* ``append_event`` is append-only, growing the per-appeal evidence timeline
  without ever mutating prior events.

This module imports only :mod:`backstop.domain` (L2 ports may depend on L1
domain alone). It performs no I/O and imports no vendor SDK.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Protocol, runtime_checkable

from backstop.domain.enums import AppealStatus
from backstop.domain.models import Appeal


@dataclass(frozen=True)
class AppealFilter:
    """Query filter for :meth:`AppealRepositoryPort.list`.

    All fields are optional; ``None`` means "do not constrain on this field".
    ``needs_human_review`` narrows to appeals flagged for a nurse.
    """

    status: Optional[AppealStatus] = None
    payer_id: Optional[str] = None
    needs_human_review: Optional[bool] = None


@dataclass(frozen=True)
class AppealPage:
    """A bounded slice of appeals returned by :meth:`AppealRepositoryPort.list`.

    ``items`` is the page of matching aggregates; ``total`` is the full match
    count across all pages (for pagination UIs); ``limit``/``offset`` echo the
    requested window.
    """

    items: List[Appeal]
    total: int
    limit: int
    offset: int


@dataclass(frozen=True)
class AppealEventRecord:
    """An append-only timeline entry attached to an appeal.

    ``seq`` is the monotonically increasing per-appeal sequence number,
    ``kind`` the event discriminator (e.g. ``"denial_parsed"``), and
    ``payload_json``/``provenance_json`` carry already-redacted JSON strings
    (PHI never reaches this record in cleartext). ``ts_iso`` is the ISO-8601
    event timestamp supplied by the injected clock.
    """

    appeal_id: str
    seq: int
    kind: str
    ts_iso: str
    payload_json: str
    provenance_json: Optional[str] = None


@runtime_checkable
class AppealRepositoryPort(Protocol):
    """Persistence port for the ``Appeal`` aggregate (CAS + append-only events).

    Implemented by ``SqliteAppealRepo`` (WAL, row caps) and ``MemoryAppealRepo``
    (bounded LRU + TTL eviction). Both honour the identical contract; services
    name this protocol and never the concrete adapter.
    """

    async def save(self, appeal: Appeal) -> None:
        """Insert or upsert an appeal aggregate.

        Persists the full aggregate including its salted PHI hashes and current
        ``version``. Implementations enforce row caps / bounded eviction so the
        store can never grow without bound.
        """
        ...

    async def load(self, appeal_id: str) -> Appeal:
        """Return the appeal with ``appeal_id``.

        Raises :class:`backstop.domain.errors.AppealNotFound` when no aggregate
        with that id exists.
        """
        ...

    async def list(
        self,
        filter: AppealFilter,
        *,
        limit: int,
        offset: int,
    ) -> AppealPage:
        """Return a bounded, filtered page of appeals.

        ``filter`` constrains the result set; ``limit``/``offset`` define the
        window. Implementations clamp ``limit`` to a safe maximum.
        """
        ...

    async def update_status_atomic(
        self,
        appeal_id: str,
        expected: AppealStatus,
        new: AppealStatus,
    ) -> bool:
        """Atomically compare-and-swap the appeal status (optimistic lock).

        Succeeds (returns ``True``) only if the persisted status currently
        equals ``expected``; on success the status becomes ``new`` and the
        aggregate ``version`` is bumped in the same transaction. Returns
        ``False`` on a stale read (another writer transitioned first). Raises
        :class:`backstop.domain.errors.AppealNotFound` if the id is unknown.
        """
        ...

    async def append_event(
        self,
        appeal_id: str,
        event: AppealEventRecord,
    ) -> None:
        """Append one timeline event to the appeal (append-only).

        Never mutates or deletes prior events. Implementations enforce
        ``UNIQUE(appeal_id, seq)`` so duplicate sequence numbers are rejected.
        Raises :class:`backstop.domain.errors.AppealNotFound` for an unknown id.
        """
        ...

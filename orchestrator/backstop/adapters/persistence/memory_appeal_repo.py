"""Bounded in-memory appeal repository (LRU + TTL) for :class:`AppealRepositoryPort`.

Implements :class:`backstop.ports.appeal_repository_port.AppealRepositoryPort`
with an in-process store that is *bounded* on two axes, closing the audited
unbounded-global-dict OOM finding:

* **LRU capacity** -- at most ``capacity`` appeals are retained; inserting beyond
  capacity evicts the least-recently-used aggregate, so memory stays bounded
  under a 10k-insert storm.
* **TTL** -- an appeal untouched for ``ttl_seconds`` (read off the injected clock)
  is treated as evicted, so stale aggregates do not pin memory.

The same async ``compare-and-swap`` and append-only-events contract as the
SQLite repo is honoured, so the two are substitutable. A single :class:`asyncio.Lock`
serialises mutations so the CAS is atomic under concurrency. This module imports
only the standard library plus :mod:`backstop.domain`/:mod:`backstop.ports`.
"""

from __future__ import annotations

import asyncio
import dataclasses
from collections import OrderedDict
from typing import List, Optional

from backstop.domain.enums import AppealStatus
from backstop.domain.errors import AppealNotFound
from backstop.domain.models import Appeal
from backstop.ports.appeal_repository_port import (
    AppealEventRecord,
    AppealFilter,
    AppealPage,
)
from backstop.ports.clock_port import ClockPort

__all__ = ["MemoryAppealRepo"]

# Hard ceiling on a single ``list`` page so a caller cannot request an unbounded
# slice (mirrors the SQLite repo's clamp).
_MAX_LIMIT = 500


@dataclasses.dataclass
class _Entry:
    """One stored aggregate plus its monotonic last-touch instant (monotonic s)."""

    appeal: Appeal
    events: List[AppealEventRecord]
    touched_monotonic: float


class MemoryAppealRepo:
    """Bounded LRU+TTL :class:`~backstop.ports.appeal_repository_port.AppealRepositoryPort`.

    Construction injects the clock (for TTL) and the ``capacity``/``ttl_seconds``
    bounds. Eviction is lazy on access plus eager on insert, so the live entry
    count never exceeds ``capacity``.
    """

    def __init__(
        self,
        *,
        clock: ClockPort,
        capacity: int = 1000,
        ttl_seconds: float = 3600.0,
    ) -> None:
        """Validate bounds and start with an empty ordered store."""
        if capacity <= 0:
            raise ValueError("capacity must be positive")
        self._clock = clock
        self._capacity = capacity
        self._ttl_seconds = ttl_seconds
        self._entries: OrderedDict[str, _Entry] = OrderedDict()
        self._lock = asyncio.Lock()

    async def save(self, appeal: Appeal) -> None:
        """Insert or replace ``appeal``, evicting LRU entries past capacity."""
        async with self._lock:
            now = self._clock.monotonic()
            self._purge_expired(now)
            existing = self._entries.get(appeal.id)
            events = existing.events if existing is not None else []
            self._entries[appeal.id] = _Entry(
                appeal=appeal,
                events=events,
                touched_monotonic=now,
            )
            self._entries.move_to_end(appeal.id)
            self._evict_over_capacity()

    async def load(self, appeal_id: str) -> Appeal:
        """Return the appeal with ``appeal_id`` or raise :class:`AppealNotFound`."""
        async with self._lock:
            entry = self._live_entry(appeal_id)
            if entry is None:
                raise AppealNotFound(appeal_id)
            self._entries.move_to_end(appeal_id)
            entry.touched_monotonic = self._clock.monotonic()
            return entry.appeal

    async def list(
        self,
        filter: AppealFilter,
        *,
        limit: int,
        offset: int,
    ) -> AppealPage:
        """Return a bounded, filtered page of appeals (insertion order)."""
        clamped_limit = max(0, min(limit, _MAX_LIMIT))
        clamped_offset = max(0, offset)
        async with self._lock:
            now = self._clock.monotonic()
            self._purge_expired(now)
            matched = [
                entry.appeal
                for entry in self._entries.values()
                if _matches(entry.appeal, filter)
            ]
            total = len(matched)
            window = matched[clamped_offset : clamped_offset + clamped_limit]
            return AppealPage(
                items=window,
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
        """Compare-and-swap the status; ``True`` only on a non-stale read."""
        async with self._lock:
            entry = self._live_entry(appeal_id)
            if entry is None:
                raise AppealNotFound(appeal_id)
            if entry.appeal.status != expected:
                return False
            entry.appeal = dataclasses.replace(entry.appeal, status=new)
            entry.touched_monotonic = self._clock.monotonic()
            self._entries.move_to_end(appeal_id)
            return True

    async def append_event(
        self,
        appeal_id: str,
        event: AppealEventRecord,
    ) -> None:
        """Append one timeline event (append-only; unique ``seq`` per appeal)."""
        async with self._lock:
            entry = self._live_entry(appeal_id)
            if entry is None:
                raise AppealNotFound(appeal_id)
            if any(existing.seq == event.seq for existing in entry.events):
                raise ValueError(
                    f"duplicate event seq {event.seq} for appeal {appeal_id}"
                )
            entry.events.append(event)
            entry.touched_monotonic = self._clock.monotonic()
            self._entries.move_to_end(appeal_id)

    # ------------------------------------------------------------------ #
    # Internals (all called under ``self._lock``).
    # ------------------------------------------------------------------ #
    def _live_entry(self, appeal_id: str) -> Optional[_Entry]:
        """Return the entry for ``appeal_id`` if present and not expired."""
        entry = self._entries.get(appeal_id)
        if entry is None:
            return None
        if self._is_expired(entry, self._clock.monotonic()):
            del self._entries[appeal_id]
            return None
        return entry

    def _is_expired(self, entry: _Entry, now: float) -> bool:
        """Return ``True`` once ``entry`` has been idle past the TTL."""
        return (now - entry.touched_monotonic) > self._ttl_seconds

    def _purge_expired(self, now: float) -> None:
        """Drop every TTL-expired entry."""
        stale = [
            appeal_id
            for appeal_id, entry in self._entries.items()
            if self._is_expired(entry, now)
        ]
        for appeal_id in stale:
            del self._entries[appeal_id]

    def _evict_over_capacity(self) -> None:
        """Evict LRU entries until the store is within ``capacity``."""
        while len(self._entries) > self._capacity:
            self._entries.popitem(last=False)

    def events_for(self, appeal_id: str) -> List[AppealEventRecord]:
        """Return a copy of the recorded events for ``appeal_id`` (test/diagnostic)."""
        entry = self._entries.get(appeal_id)
        if entry is None:
            return []
        return list(entry.events)


def _matches(appeal: Appeal, filter: AppealFilter) -> bool:
    """Return ``True`` if ``appeal`` satisfies every set field of ``filter``."""
    if filter.status is not None and appeal.status != filter.status:
        return False
    if filter.payer_id is not None and appeal.denial.payer.payer_id != filter.payer_id:
        return False
    if (
        filter.needs_human_review is not None
        and appeal.needs_human_review != filter.needs_human_review
    ):
        return False
    return True

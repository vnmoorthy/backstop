"""Persistence adapters for the :class:`AppealRepositoryPort`.

Houses the bounded in-memory repo
(:class:`~backstop.adapters.persistence.memory_appeal_repo.MemoryAppealRepo`,
LRU + TTL eviction) and the durable SQLite repo
(:class:`~backstop.adapters.persistence.sqlite_appeal_repo.SqliteAppealRepo`,
WAL + compare-and-swap status update + append-only events). Both honour the
identical port contract; services name the port and never the concrete adapter.
"""

from __future__ import annotations

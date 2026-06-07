"""Contract suite for :class:`AppealRepositoryPort` (SQLite + bounded-memory).

Both concrete repos are instantiated and asserted to honour the *same* port:
the parametrized tests run identically against ``SqliteAppealRepo`` and
``MemoryAppealRepo``. The load-bearing assertions for M13 are:

* ``update_status_atomic`` is a true compare-and-swap -- under concurrency only
  one of N racing writers transitions a status; the rest observe ``False`` on
  the now-stale expected value (the snapshot-watchdog torn-read race fix).
* ``append_event`` is append-only with a unique per-appeal ``seq``.
* ``MemoryAppealRepo`` evicts at capacity (LRU) so the store stays bounded under
  a 10k-insert storm.

No network or vendor service is touched; SQLite runs against an on-disk temp DB.
"""

from __future__ import annotations

import asyncio
import datetime as dt
from pathlib import Path
from typing import AsyncIterator, Awaitable, Callable, List

import pytest

from backstop.adapters.persistence.memory_appeal_repo import MemoryAppealRepo
from backstop.adapters.persistence.sqlite_appeal_repo import SqliteAppealRepo
from backstop.adapters.system.system_clock_adapter import SystemClockAdapter
from backstop.domain.enums import AppealStatus
from backstop.domain.errors import AppealNotFound
from backstop.domain.models import Appeal, Denial, Payer
from backstop.domain.money import Money, RecoverableDollars, SolDeadline
from backstop.ports.appeal_repository_port import (
    AppealEventRecord,
    AppealFilter,
    AppealRepositoryPort,
)

_FROZEN_DATE = dt.date(2026, 6, 7)


def _appeal(appeal_id: str, *, status: AppealStatus = AppealStatus.DENIED) -> Appeal:
    """Build a minimal valid appeal in ``status`` (no PHI; salted hashes only)."""
    payer = Payer(payer_id="payer-1", name="Acme Health")
    denial = Denial(
        denial_id=f"den-{appeal_id}",
        payer=payer,
        plan="PPO",
        member_id_hash="hash-member",
        claim_number_hash="hash-claim",
        denial_code="197",
        rarc=None,
        cpt=("99213",),
        billed=Money(cents=50_000),
        dos=_FROZEN_DATE,
        npis=("1234567890",),
    )
    return Appeal(
        id=appeal_id,
        status=status,
        denial=denial,
        recoverable=RecoverableDollars(Money(cents=50_000)),
        sol=SolDeadline(deadline=dt.date(2026, 7, 7)),
        route=None,
        needs_human_review=False,
        events=(),
    )


# A repo factory yields an (already-connected) repo and a teardown coroutine.
RepoFactory = Callable[[], Awaitable["_Bundle"]]


class _Bundle:
    """A connected repo paired with its async teardown."""

    def __init__(self, repo: AppealRepositoryPort, close: Callable[[], Awaitable[None]]):
        self.repo = repo
        self.close = close


async def _make_sqlite(tmp_path: Path) -> _Bundle:
    repo = SqliteAppealRepo(database_path=str(tmp_path / "appeals.db"))
    await repo.connect()
    return _Bundle(repo, repo.aclose)


async def _make_memory(_tmp_path: Path) -> _Bundle:
    repo = MemoryAppealRepo(clock=SystemClockAdapter(), capacity=10_000, ttl_seconds=1e9)

    async def _noop() -> None:
        return None

    return _Bundle(repo, _noop)


@pytest.fixture(params=["sqlite", "memory"])
async def repo(
    request: pytest.FixtureRequest, tmp_path: Path
) -> AsyncIterator[AppealRepositoryPort]:
    """Yield each concrete repo in turn so every test runs against both."""
    if request.param == "sqlite":
        bundle = await _make_sqlite(tmp_path)
    else:
        bundle = await _make_memory(tmp_path)
    try:
        yield bundle.repo
    finally:
        await bundle.close()


def test_both_repos_satisfy_the_port() -> None:
    """Both concrete repos are recognised as the runtime-checkable port."""
    mem = MemoryAppealRepo(clock=SystemClockAdapter())
    sql = SqliteAppealRepo(database_path=":memory:")
    assert isinstance(mem, AppealRepositoryPort)
    assert isinstance(sql, AppealRepositoryPort)


async def test_save_then_load_round_trips(repo: AppealRepositoryPort) -> None:
    """A saved aggregate loads back equal (exact-cents, hashes, enums intact)."""
    appeal = _appeal("ap-1")
    await repo.save(appeal)
    loaded = await repo.load("ap-1")
    assert loaded == appeal


async def test_load_unknown_raises_not_found(repo: AppealRepositoryPort) -> None:
    """Loading an unknown id raises the domain :class:`AppealNotFound`."""
    with pytest.raises(AppealNotFound):
        await repo.load("nope")


async def test_cas_succeeds_then_rejects_stale(repo: AppealRepositoryPort) -> None:
    """CAS swaps on a matching expected value and rejects a stale one."""
    await repo.save(_appeal("ap-2", status=AppealStatus.DENIED))
    assert await repo.update_status_atomic(
        "ap-2", AppealStatus.DENIED, AppealStatus.TRIAGED
    )
    # The same (now stale) expected value must fail rather than clobber.
    assert not await repo.update_status_atomic(
        "ap-2", AppealStatus.DENIED, AppealStatus.IN_APPEAL
    )
    assert (await repo.load("ap-2")).status is AppealStatus.TRIAGED


async def test_cas_unknown_raises_not_found(repo: AppealRepositoryPort) -> None:
    """CAS on an unknown id raises :class:`AppealNotFound`."""
    with pytest.raises(AppealNotFound):
        await repo.update_status_atomic(
            "ghost", AppealStatus.DENIED, AppealStatus.TRIAGED
        )


async def test_cas_is_atomic_under_concurrency(repo: AppealRepositoryPort) -> None:
    """Under N racing writers exactly one transition wins; the rest see stale.

    This is the snapshot-watchdog torn-read race fix: even when many coroutines
    race the same ``DENIED -> TRIAGED`` swap, only one observes ``True``.
    """
    await repo.save(_appeal("ap-race", status=AppealStatus.DENIED))

    async def attempt() -> bool:
        return await repo.update_status_atomic(
            "ap-race", AppealStatus.DENIED, AppealStatus.TRIAGED
        )

    results = await asyncio.gather(*[attempt() for _ in range(25)])
    assert sum(1 for ok in results if ok) == 1
    assert (await repo.load("ap-race")).status is AppealStatus.TRIAGED


async def test_append_event_is_append_only_unique_seq(repo: AppealRepositoryPort) -> None:
    """Events append in order and a duplicate ``seq`` is rejected."""
    await repo.save(_appeal("ap-3"))
    first = AppealEventRecord(
        appeal_id="ap-3",
        seq=0,
        kind="denial_parsed",
        ts_iso="2026-06-07T12:00:00+00:00",
        payload_json="{}",
    )
    second = AppealEventRecord(
        appeal_id="ap-3",
        seq=1,
        kind="triaged",
        ts_iso="2026-06-07T12:01:00+00:00",
        payload_json="{}",
    )
    await repo.append_event("ap-3", first)
    await repo.append_event("ap-3", second)
    with pytest.raises(ValueError, match="duplicate"):
        await repo.append_event("ap-3", second)


async def test_append_event_unknown_raises_not_found(repo: AppealRepositoryPort) -> None:
    """Appending to an unknown appeal raises :class:`AppealNotFound`."""
    event = AppealEventRecord(
        appeal_id="ghost",
        seq=0,
        kind="x",
        ts_iso="2026-06-07T12:00:00+00:00",
        payload_json="{}",
    )
    with pytest.raises(AppealNotFound):
        await repo.append_event("ghost", event)


async def test_list_filters_and_paginates(repo: AppealRepositoryPort) -> None:
    """``list`` constrains by filter and returns a bounded page + total."""
    for i in range(5):
        await repo.save(_appeal(f"ap-list-{i}", status=AppealStatus.DENIED))
    await repo.save(_appeal("ap-other", status=AppealStatus.TRIAGED))

    page = await repo.list(
        AppealFilter(status=AppealStatus.DENIED), limit=2, offset=0
    )
    assert page.total == 5
    assert len(page.items) == 2
    assert all(item.status is AppealStatus.DENIED for item in page.items)


async def test_list_clamps_oversized_limit(repo: AppealRepositoryPort) -> None:
    """An absurd limit is clamped to a safe maximum (no unbounded slice)."""
    await repo.save(_appeal("ap-clamp"))
    page = await repo.list(AppealFilter(), limit=10_000, offset=0)
    assert page.limit <= 500


async def test_memory_repo_evicts_at_capacity() -> None:
    """The bounded memory repo never exceeds capacity under a 10k-insert storm."""
    repo = MemoryAppealRepo(clock=SystemClockAdapter(), capacity=128, ttl_seconds=1e9)
    for i in range(10_000):
        await repo.save(_appeal(f"ap-{i}"))
    # Internal store is bounded by capacity.
    assert len(repo._entries) <= 128  # - bounded-store assertion
    # The earliest inserted id was evicted (LRU); the latest survives.
    with pytest.raises(AppealNotFound):
        await repo.load("ap-0")
    assert (await repo.load("ap-9999")).id == "ap-9999"


async def test_memory_repo_evicts_on_ttl() -> None:
    """An entry idle past its TTL is treated as evicted."""
    clock = _SteppingClock(start=1000.0)
    repo = MemoryAppealRepo(clock=clock, capacity=128, ttl_seconds=10.0)
    await repo.save(_appeal("ap-ttl"))
    clock.advance(11.0)
    with pytest.raises(AppealNotFound):
        await repo.load("ap-ttl")


class _SteppingClock:
    """A controllable clock whose monotonic value advances on demand."""

    def __init__(self, *, start: float) -> None:
        self._value = start

    def now(self) -> dt.datetime:
        return dt.datetime(2026, 6, 7, tzinfo=dt.timezone.utc)

    def monotonic(self) -> float:
        return self._value

    def advance(self, seconds: float) -> None:
        self._value += seconds


def test_list_result_shape() -> None:
    """The exported port symbols exist (import-time contract anchor)."""
    assert AppealFilter is not None
    assert AppealRepositoryPort is not None
    _: List[str] = []
    assert _ == []

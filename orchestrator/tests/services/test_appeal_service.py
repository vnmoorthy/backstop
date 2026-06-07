"""Tests for :class:`AppealService`.

Pins: creating an appeal computes the route via the pure policy, mints an id,
persists a ``DENIED`` aggregate with an opening event, and that lifecycle
advances go through the optimistic-lock CAS (a stale CAS writes no event).
"""

from __future__ import annotations

from backstop.domain.carc_table import CarcEntry, CarcTable
from backstop.domain.enums import AppealStatus, RouteDecision
from backstop.domain.money import Money, RecoverableDollars, SolDeadline
from backstop.services.appeal_service import AppealService
from tests.services.fakes import FakeClock, FakeIdGen, MemoryAppealRepo, make_appeal


def _table() -> CarcTable:
    """A tiny CARC table: 197 → APPEAL, 45 → WRITE_OFF."""
    return CarcTable(
        entries={
            "197": CarcEntry(
                code="197",
                category="prior_auth",
                canonical_reason="precert absent",
                default_route=RouteDecision.APPEAL,
            ),
            "45": CarcEntry(
                code="45",
                category="contractual",
                canonical_reason="over fee schedule",
                default_route=RouteDecision.WRITE_OFF,
            ),
        }
    )


def _service(repo: MemoryAppealRepo) -> AppealService:
    """Build an appeal service over the repo, fixed clock, and id gen."""
    return AppealService(
        repo=repo,
        clock=FakeClock(),
        id_gen=FakeIdGen("appeal"),
        carc_table=_table(),
    )


async def test_create_routes_persists_and_logs_opening_event() -> None:
    """Create mints an id, routes the denial, saves it, and logs an event."""
    repo = MemoryAppealRepo()
    service = _service(repo)
    denial = make_appeal(denial_code="197").denial

    result = await service.create(
        denial,
        recoverable=RecoverableDollars(Money(cents=40_000)),
        sol=SolDeadline.from_iso("2026-07-07"),
    )

    assert result.route is RouteDecision.APPEAL
    assert result.appeal.id == "appeal-0001"
    assert result.appeal.status is AppealStatus.DENIED
    stored = await repo.load("appeal-0001")
    assert stored.route is RouteDecision.APPEAL
    assert [e.kind for e in repo.events_for("appeal-0001")] == ["denial_received"]


async def test_create_uses_write_off_route_for_contractual() -> None:
    """A CARC mapped to WRITE_OFF routes accordingly."""
    repo = MemoryAppealRepo()
    service = _service(repo)
    denial = make_appeal(denial_code="45").denial

    result = await service.create(
        denial,
        recoverable=RecoverableDollars.zero(),
        sol=SolDeadline.from_iso("2026-07-07"),
    )

    assert result.route is RouteDecision.WRITE_OFF


async def test_unknown_carc_falls_back_to_resubmit() -> None:
    """An unknown CARC uses the conservative fallback route."""
    repo = MemoryAppealRepo()
    service = _service(repo)
    denial = make_appeal(denial_code="ZZ").denial

    result = await service.create(
        denial,
        recoverable=RecoverableDollars.zero(),
        sol=SolDeadline.from_iso("2026-07-07"),
    )

    assert result.route is RouteDecision.RESUBMIT


async def test_advance_uses_cas_and_logs_event() -> None:
    """A winning CAS transitions the status and appends the event."""
    repo = MemoryAppealRepo()
    await repo.save(make_appeal("appeal-1", status=AppealStatus.DENIED))
    service = _service(repo)

    won = await service.advance(
        "appeal-1",
        expected=AppealStatus.DENIED,
        new=AppealStatus.TRIAGED,
        event_kind="triaged",
        seq=0,
    )

    assert won is True
    assert (await repo.load("appeal-1")).status is AppealStatus.TRIAGED
    assert [e.kind for e in repo.events_for("appeal-1")] == ["triaged"]
    assert repo.cas_calls == [
        ("appeal-1", AppealStatus.DENIED, AppealStatus.TRIAGED)
    ]


async def test_stale_cas_writes_no_event() -> None:
    """A losing CAS (wrong expected status) advances nothing and logs nothing."""
    repo = MemoryAppealRepo()
    await repo.save(make_appeal("appeal-1", status=AppealStatus.TRIAGED))
    service = _service(repo)

    won = await service.advance(
        "appeal-1",
        expected=AppealStatus.DENIED,  # wrong: it is TRIAGED
        new=AppealStatus.IN_APPEAL,
        event_kind="appeal_opened",
        seq=0,
    )

    assert won is False
    assert (await repo.load("appeal-1")).status is AppealStatus.TRIAGED
    assert repo.events_for("appeal-1") == []

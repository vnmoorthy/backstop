"""Tests for :class:`TriageService` worklist ordering.

The worklist ranks appeals by recoverable dollars weighted by SOL urgency
(``recoverable-$ x urgency``), most-urgent first. The clock is fixed so the
math is deterministic.
"""

from __future__ import annotations

from backstop.domain.enums import AppealStatus
from backstop.services.triage_service import TriageService
from tests.services.fakes import FakeClock, MemoryAppealRepo, make_appeal

# Frozen "now" is 2026-06-07 (FakeClock default).


def _service(repo: MemoryAppealRepo) -> TriageService:
    """Build a triage service over the given repo and a fixed clock."""
    return TriageService(repo, FakeClock())


def test_ranks_by_recoverable_times_urgency() -> None:
    """A nearer deadline and more money both raise the rank."""
    # big money, far deadline → high value but low urgency.
    far_big = make_appeal("a-far-big", recoverable_cents=200_000, sol_iso="2026-12-31")
    # small money, imminent deadline → max urgency.
    near_small = make_appeal("a-near", recoverable_cents=40_000, sol_iso="2026-06-08")
    # big money AND imminent → should top the list.
    near_big = make_appeal("a-near-big", recoverable_cents=200_000, sol_iso="2026-06-08")

    service = _service(MemoryAppealRepo())
    ranked = service.rank([far_big, near_small, near_big])

    ids = [item.appeal.id for item in ranked]
    assert ids[0] == "a-near-big"
    # values are strictly non-increasing.
    values = [item.score.value for item in ranked]
    assert values == sorted(values, reverse=True)


def test_urgency_breaks_equal_money() -> None:
    """At equal recoverable dollars, the nearer SOL ranks first."""
    soon = make_appeal("soon", recoverable_cents=100_000, sol_iso="2026-06-10")
    later = make_appeal("later", recoverable_cents=100_000, sol_iso="2026-09-01")

    ranked = _service(MemoryAppealRepo()).rank([later, soon])

    assert ranked[0].appeal.id == "soon"


def test_money_breaks_equal_urgency() -> None:
    """At equal (max) urgency, more recoverable money ranks first."""
    # Both within the fuse window → urgency pinned at 1.0.
    small = make_appeal("small", recoverable_cents=10_000, sol_iso="2026-06-08")
    large = make_appeal("large", recoverable_cents=90_000, sol_iso="2026-06-08")

    ranked = _service(MemoryAppealRepo()).rank([small, large])

    assert ranked[0].appeal.id == "large"


def test_ranking_is_deterministic() -> None:
    """Repeated ranking of the same input yields the same order."""
    appeals = [
        make_appeal("x", recoverable_cents=30_000, sol_iso="2026-08-01"),
        make_appeal("y", recoverable_cents=70_000, sol_iso="2026-06-20"),
        make_appeal("z", recoverable_cents=70_000, sol_iso="2026-06-20"),
    ]
    service = _service(MemoryAppealRepo())
    first = [i.appeal.id for i in service.rank(appeals)]
    second = [i.appeal.id for i in service.rank(appeals)]
    assert first == second


async def test_worklist_loads_and_ranks_from_repo() -> None:
    """``worklist`` reads a page from the repo and returns it ranked."""
    repo = MemoryAppealRepo()
    await repo.save(make_appeal("lo", recoverable_cents=20_000, sol_iso="2026-12-01"))
    await repo.save(make_appeal("hi", recoverable_cents=80_000, sol_iso="2026-06-08"))

    ranked = await _service(repo).worklist(limit=10, offset=0)

    assert ranked[0].appeal.id == "hi"
    assert len(ranked) == 2


async def test_worklist_can_filter_by_status() -> None:
    """The worklist honours a repository filter."""
    from backstop.ports.appeal_repository_port import AppealFilter

    repo = MemoryAppealRepo()
    await repo.save(make_appeal("denied", status=AppealStatus.DENIED))
    await repo.save(make_appeal("triaged", status=AppealStatus.TRIAGED))

    ranked = await _service(repo).worklist(
        filter=AppealFilter(status=AppealStatus.TRIAGED)
    )

    assert [i.appeal.id for i in ranked] == ["triaged"]

"""Unit tests for :mod:`backstop.domain.triage`.

The triage score ranks appeals by recoverable dollars weighted by
statute-of-limitations urgency: more money and a nearer deadline both raise the
score. These tests pin the two monotonicity axes independently (money at fixed
SOL, SOL at fixed money), determinism across repeated calls, and the absence of
side effects.

The :func:`_appeal` helper builds a real, minimal :class:`Appeal` whose
recoverable amount and SOL deadline are varied per case while every other field
is held constant, so the scoring math is exercised against the genuine domain
entity rather than a stub.
"""

from __future__ import annotations

import datetime as _dt

from backstop.domain.enums import AppealStatus
from backstop.domain.models import Appeal, Denial, Payer
from backstop.domain.money import Money, RecoverableDollars, SolDeadline
from backstop.domain.triage import TriageScore, score

_PAYER = Payer(payer_id="payer-1", name="Acme Health")


def _denial() -> Denial:
    """Build a constant denial; triage never reads its fields."""
    return Denial(
        denial_id="denial-1",
        payer=_PAYER,
        plan="PPO",
        member_id_hash="hash-member",
        claim_number_hash="hash-claim",
        denial_code="197",
        rarc=None,
        cpt=("99213",),
        billed=Money(cents=50_000),
        dos=_dt.date(2026, 6, 1),
    )


def _appeal(cents: int, deadline: _dt.date) -> Appeal:
    """Build a DENIED appeal with the given recoverable amount and SOL date."""
    return Appeal(
        id="appeal-1",
        status=AppealStatus.DENIED,
        denial=_denial(),
        recoverable=RecoverableDollars(Money(cents=cents)),
        sol=SolDeadline(deadline=deadline),
    )


# --------------------------------------------------------------------------- #
# Monotonic in recoverable dollars (SOL held fixed).
# --------------------------------------------------------------------------- #
def test_higher_recoverable_scores_higher_at_equal_sol(frozen_date: _dt.date) -> None:
    """With identical deadlines, more recoverable money is more urgent."""
    deadline = _dt.date(2026, 7, 7)
    low = score(_appeal(cents=10_000, deadline=deadline), frozen_date)
    high = score(_appeal(cents=500_000, deadline=deadline), frozen_date)
    assert high.value > low.value


# --------------------------------------------------------------------------- #
# Monotonic in SOL urgency (dollars held fixed).
# --------------------------------------------------------------------------- #
def test_nearer_sol_scores_higher_at_equal_dollars(frozen_date: _dt.date) -> None:
    """With identical dollars, a nearer deadline is more urgent."""
    far = score(_appeal(cents=100_000, deadline=_dt.date(2026, 12, 7)), frozen_date)
    near = score(_appeal(cents=100_000, deadline=_dt.date(2026, 6, 10)), frozen_date)
    assert near.value > far.value


def test_urgency_rises_as_deadline_approaches(frozen_date: _dt.date) -> None:
    """The ``urgency`` component is non-decreasing as the deadline nears."""
    distant = score(_appeal(cents=100_000, deadline=_dt.date(2026, 12, 1)), frozen_date)
    soon = score(_appeal(cents=100_000, deadline=_dt.date(2026, 6, 12)), frozen_date)
    overdue = score(_appeal(cents=100_000, deadline=_dt.date(2026, 6, 1)), frozen_date)
    assert distant.urgency < soon.urgency <= overdue.urgency
    assert overdue.urgency == 1.0


# --------------------------------------------------------------------------- #
# Determinism + purity.
# --------------------------------------------------------------------------- #
def test_score_is_deterministic(frozen_date: _dt.date) -> None:
    """Scoring the same appeal twice yields the identical score."""
    appeal = _appeal(cents=250_000, deadline=_dt.date(2026, 8, 1))
    assert score(appeal, frozen_date) == score(appeal, frozen_date)


def test_score_preserves_components(frozen_date: _dt.date) -> None:
    """The score retains its recoverable + urgency components for audit/UI."""
    result = score(_appeal(cents=250_000, deadline=_dt.date(2026, 8, 1)), frozen_date)
    assert result.recoverable_cents == 250_000
    assert 0.0 <= result.urgency <= 1.0


def test_score_has_no_side_effects(frozen_date: _dt.date) -> None:
    """Scoring must not mutate the appeal it is handed (frozen, but explicit)."""
    appeal = _appeal(cents=250_000, deadline=_dt.date(2026, 8, 1))
    before = (appeal.recoverable.cents, appeal.sol)
    score(appeal, frozen_date)
    assert (appeal.recoverable.cents, appeal.sol) == before


def test_full_ordering_combines_both_axes(frozen_date: _dt.date) -> None:
    """A queue sorted by score puts big-and-urgent ahead of small-and-distant."""
    big_urgent = _appeal(cents=900_000, deadline=_dt.date(2026, 6, 9))
    big_distant = _appeal(cents=900_000, deadline=_dt.date(2027, 1, 1))
    small_urgent = _appeal(cents=5_000, deadline=_dt.date(2026, 6, 9))

    ranked = sorted(
        [big_distant, small_urgent, big_urgent],
        key=lambda a: score(a, frozen_date).value,
        reverse=True,
    )
    assert ranked[0] is big_urgent
    assert ranked[-1] is small_urgent


def test_triage_score_is_orderable() -> None:
    """``TriageScore`` orders by ``value`` (least to most urgent)."""
    lo = TriageScore(value=1.0, recoverable_cents=100, urgency=0.1)
    hi = TriageScore(value=9.0, recoverable_cents=900, urgency=1.0)
    assert lo < hi
    assert max(lo, hi) is hi

"""Unit tests for :mod:`backstop.domain.money`.

Covers the integer-cents :class:`Money` value object -- construction,
addition/subtraction/integer multiplication, ``$`` formatting, the
:class:`RecoverableDollars` wrapper, and the :class:`SolDeadline`
``days_remaining`` calculation. The suite also pins the *no-float* invariant:
the internal representation must be a plain ``int`` number of cents, never a
binary float.
"""

from __future__ import annotations

import datetime as _dt

import pytest

from backstop.domain.money import Money, RecoverableDollars, SolDeadline


# --------------------------------------------------------------------------- #
# Money: construction + the no-float invariant.
# --------------------------------------------------------------------------- #
def test_money_holds_integer_cents() -> None:
    """``Money(cents=...)`` stores an exact integer count of cents."""
    m = Money(cents=12345)
    assert m.cents == 12345
    assert isinstance(m.cents, int)


def test_money_repr_is_never_a_float() -> None:
    """The internal amount must be ``int``; no float may leak in."""
    m = Money(cents=99)
    assert isinstance(m.cents, int)
    # bool is an int subclass; exclude it so "amount is an integer" is exact.
    assert not isinstance(m.cents, bool)
    assert not isinstance(m.cents, float)


def test_money_rejects_float_cents() -> None:
    """Passing a float for cents is a programming error and must raise."""
    with pytest.raises((TypeError, ValueError)):
        Money(cents=1.5)  # type: ignore[arg-type]


def test_money_rejects_bool_cents() -> None:
    """``bool`` is an ``int`` subclass but is not a valid money amount."""
    with pytest.raises((TypeError, ValueError)):
        Money(cents=True)  # type: ignore[arg-type]


def test_money_from_dollars() -> None:
    """``from_dollars`` builds exact cents from whole dollars + cents."""
    assert Money.from_dollars(123, 45) == Money(cents=12345)
    assert Money.from_dollars(1) == Money(cents=100)


def test_money_zero_is_additive_identity() -> None:
    """``Money.zero()`` is ``$0.00`` and adds without changing a value."""
    assert Money.zero() == Money(cents=0)
    assert Money(cents=500) + Money.zero() == Money(cents=500)


# --------------------------------------------------------------------------- #
# Money: arithmetic returns Money and is exact.
# --------------------------------------------------------------------------- #
def test_money_addition() -> None:
    """``+`` adds cents and yields a new ``Money``."""
    result = Money(cents=150) + Money(cents=75)
    assert result == Money(cents=225)
    assert isinstance(result, Money)


def test_money_subtraction() -> None:
    """``-`` subtracts cents and yields a new ``Money``."""
    result = Money(cents=500) - Money(cents=125)
    assert result == Money(cents=375)


def test_money_multiplication_by_int_scalar() -> None:
    """``*`` scales by an integer and stays exact (no rounding drift)."""
    result = Money(cents=333) * 3
    assert result == Money(cents=999)
    assert isinstance(result, Money)


def test_money_right_multiplication() -> None:
    """``int * Money`` is supported and commutative."""
    assert 3 * Money(cents=333) == Money(cents=999)


def test_money_rejects_float_multiplication() -> None:
    """Scaling by a float would introduce rounding drift and must raise."""
    with pytest.raises(TypeError):
        Money(cents=100) * 1.5  # type: ignore[operator]


def test_money_arithmetic_is_immutable() -> None:
    """Arithmetic never mutates an operand in place."""
    original = Money(cents=100)
    _ = original + Money(cents=50)
    assert original.cents == 100


def test_money_equality_and_ordering() -> None:
    """Two ``Money`` of equal cents are equal; ordering is by cents."""
    assert Money(cents=100) == Money(cents=100)
    assert Money(cents=100) != Money(cents=101)
    assert Money(cents=99) < Money(cents=100)
    assert Money(cents=200) > Money(cents=199)


def test_money_is_frozen() -> None:
    """The value object is immutable -- assigning to ``cents`` raises."""
    m = Money(cents=10)
    with pytest.raises((AttributeError, TypeError)):
        m.cents = 20  # type: ignore[misc]


# --------------------------------------------------------------------------- #
# Money: dollar formatting.
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("cents", "expected"),
    [
        (0, "$0.00"),
        (5, "$0.05"),
        (45, "$0.45"),
        (100, "$1.00"),
        (12345, "$123.45"),
        (123450, "$1,234.50"),
    ],
)
def test_money_dollar_format(cents: int, expected: str) -> None:
    """``format`` renders a ``$D.CC`` string with two-cent precision."""
    assert Money(cents=cents).format() == expected
    # ``str`` delegates to ``format`` for the same rendering.
    assert str(Money(cents=cents)) == expected


def test_money_negative_dollar_format() -> None:
    """A negative balance renders with a leading minus sign."""
    assert Money(cents=-150).format() == "-$1.50"


# --------------------------------------------------------------------------- #
# RecoverableDollars: a Money wrapper.
# --------------------------------------------------------------------------- #
def test_recoverable_dollars_wraps_money() -> None:
    """``RecoverableDollars`` carries a recoverable :class:`Money` amount."""
    rec = RecoverableDollars(Money(cents=250000))
    assert rec.amount == Money(cents=250000)
    assert rec.cents == 250000


def test_recoverable_dollars_rejects_non_money() -> None:
    """The wrapper requires a :class:`Money`, not a bare int."""
    with pytest.raises(TypeError):
        RecoverableDollars(250000)  # type: ignore[arg-type]


def test_recoverable_dollars_formats_as_dollars() -> None:
    """A recoverable amount formats with the same ``$`` rules as ``Money``."""
    assert RecoverableDollars(Money(cents=250000)).format() == "$2,500.00"


def test_recoverable_dollars_zero() -> None:
    """``RecoverableDollars.zero()`` is zero recoverable dollars."""
    assert RecoverableDollars.zero().cents == 0


# --------------------------------------------------------------------------- #
# SolDeadline: days_remaining(now).
# --------------------------------------------------------------------------- #
def test_sol_deadline_days_remaining_future(frozen_date: _dt.date) -> None:
    """A deadline 30 days out reports 30 days remaining at the frozen now."""
    deadline = SolDeadline(deadline=_dt.date(2026, 7, 7))  # +30 days from 06-07
    assert deadline.days_remaining(frozen_date) == 30


def test_sol_deadline_days_remaining_today(frozen_date: _dt.date) -> None:
    """A deadline equal to today reports zero days remaining."""
    deadline = SolDeadline(deadline=_dt.date(2026, 6, 7))
    assert deadline.days_remaining(frozen_date) == 0


def test_sol_deadline_days_remaining_past_is_negative(
    frozen_date: _dt.date,
) -> None:
    """A deadline already passed reports a negative count (overdue)."""
    deadline = SolDeadline(deadline=_dt.date(2026, 6, 2))  # -5 days
    assert deadline.days_remaining(frozen_date) == -5


def test_sol_deadline_is_expired(frozen_date: _dt.date) -> None:
    """``is_expired`` flips ``True`` strictly past the deadline."""
    assert SolDeadline(deadline=_dt.date(2026, 6, 6)).is_expired(frozen_date) is True
    assert SolDeadline(deadline=_dt.date(2026, 6, 7)).is_expired(frozen_date) is False


def test_sol_deadline_from_iso_round_trip() -> None:
    """``from_iso`` parses an ISO date and ``.iso`` renders it back."""
    deadline = SolDeadline.from_iso("2026-07-07")
    assert deadline.deadline == _dt.date(2026, 7, 7)
    assert deadline.iso == "2026-07-07"
    assert deadline.days_remaining(_dt.date(2026, 6, 7)) == 30

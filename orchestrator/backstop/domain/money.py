"""Money and deadline value objects for the Backstop appeals kernel.

All monetary amounts are integer cents — there is no ``float`` anywhere in this
module, so rounding drift cannot accumulate across triage scoring, ledgers, and
appeal totals. :class:`SolDeadline` models a statute-of-limitations filing date
and answers "how many days remain?" relative to a caller-supplied ``now``.

Pure domain code: standard library only, no I/O, no vendor dependencies.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

__all__ = ["Money", "RecoverableDollars", "SolDeadline"]


@dataclass(frozen=True, order=True)
class Money:
    """An exact monetary amount stored as integer cents.

    Supports addition, subtraction, and integer scaling while remaining a
    frozen value object. Comparison and ordering follow ``cents``. Amounts may
    be negative (e.g. an adjustment), but the type never silently coerces a
    ``float``.
    """

    cents: int

    def __post_init__(self) -> None:
        """Reject non-integer cents to keep the no-float invariant total."""
        if not isinstance(self.cents, int) or isinstance(self.cents, bool):
            raise TypeError(f"Money.cents must be an int, got {type(self.cents)!r}")

    @classmethod
    def zero(cls) -> Money:
        """Return the additive identity, ``$0.00``."""
        return cls(0)

    @classmethod
    def from_dollars(cls, dollars: int, cents: int = 0) -> Money:
        """Build :class:`Money` from whole dollars plus optional cents."""
        if not isinstance(dollars, int) or isinstance(dollars, bool):
            raise TypeError("dollars must be an int")
        if not isinstance(cents, int) or isinstance(cents, bool):
            raise TypeError("cents must be an int")
        sign = -1 if dollars < 0 else 1
        return cls(dollars * 100 + sign * cents)

    def __add__(self, other: Money) -> Money:
        """Return the sum of two amounts."""
        if not isinstance(other, Money):
            return NotImplemented
        return Money(self.cents + other.cents)

    def __sub__(self, other: Money) -> Money:
        """Return the difference of two amounts."""
        if not isinstance(other, Money):
            return NotImplemented
        return Money(self.cents - other.cents)

    def __mul__(self, factor: int) -> Money:
        """Scale the amount by an integer ``factor`` (kept exact)."""
        if not isinstance(factor, int) or isinstance(factor, bool):
            raise TypeError("Money can only be multiplied by an int")
        return Money(self.cents * factor)

    def __rmul__(self, factor: int) -> Money:
        """Support ``int * Money`` (commutative scaling)."""
        return self.__mul__(factor)

    def __neg__(self) -> Money:
        """Return the negated amount."""
        return Money(-self.cents)

    @property
    def dollars(self) -> int:
        """Whole-dollar component, truncated toward zero."""
        return abs(self.cents) // 100 * (-1 if self.cents < 0 else 1)

    def format(self) -> str:
        """Render as a ``$``-prefixed string, e.g. ``$1,234.50`` / ``-$0.07``."""
        sign = "-" if self.cents < 0 else ""
        whole, frac = divmod(abs(self.cents), 100)
        return f"{sign}${whole:,}.{frac:02d}"

    def __str__(self) -> str:
        """Return the human-readable ``format`` rendering."""
        return self.format()


@dataclass(frozen=True, order=True)
class RecoverableDollars:
    """The dollar amount judged recoverable for an appeal.

    Wraps a :class:`Money` to make intent explicit at call sites (a function
    asking for ``RecoverableDollars`` will not accept an arbitrary ``Money``
    such as the billed total) while reusing the exact-cents arithmetic.
    """

    amount: Money

    def __post_init__(self) -> None:
        """Validate the wrapped amount is :class:`Money`."""
        if not isinstance(self.amount, Money):
            raise TypeError("RecoverableDollars wraps a Money instance")

    @classmethod
    def zero(cls) -> RecoverableDollars:
        """Return zero recoverable dollars."""
        return cls(Money.zero())

    @property
    def cents(self) -> int:
        """Recoverable value expressed in integer cents."""
        return self.amount.cents

    def format(self) -> str:
        """Render the recoverable amount as a ``$`` string."""
        return self.amount.format()

    def __str__(self) -> str:
        """Return the human-readable rendering."""
        return self.amount.format()


@dataclass(frozen=True, order=True)
class SolDeadline:
    """A statute-of-limitations filing deadline as an ISO calendar date.

    The deadline is stored as a :class:`datetime.date`; "now" is always passed
    in so this value object stays pure and deterministic (no clock access).
    """

    deadline: date

    @classmethod
    def from_iso(cls, iso_date: str) -> SolDeadline:
        """Parse an ``YYYY-MM-DD`` ISO date string into a deadline."""
        return cls(date.fromisoformat(iso_date))

    @property
    def iso(self) -> str:
        """The deadline as an ``YYYY-MM-DD`` ISO string."""
        return self.deadline.isoformat()

    def days_remaining(self, now: date) -> int:
        """Whole days from ``now`` until the deadline (negative if past)."""
        return (self.deadline - now).days

    def is_expired(self, now: date) -> bool:
        """Return ``True`` once ``now`` is strictly past the deadline."""
        return self.days_remaining(now) < 0

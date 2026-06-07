"""Shared pytest fixtures for the ``backstop`` test suite.

Provides deterministic test doubles for the two ambient dependencies the
domain logic is parameterised over -- the wall clock and identifier
generation -- so that unit tests stay pure and reproducible:

* :func:`frozen_now` / :func:`frozen_clock` -- a fixed "now" instant and a
  zero-argument callable returning it, for SOL-deadline and triage math.
* :func:`fake_id_gen` -- a monotonic, prefixable id factory standing in for a
  UUID/ULID generator.

No production code is imported here, so these fixtures are usable even before
the domain modules are implemented.
"""

from __future__ import annotations

import datetime as _dt
import itertools
from typing import Callable, Iterator

import pytest

# NOTE: domain imports are performed lazily inside the appeal-factory fixture so
# that the time/id fixtures above remain usable even before the domain package
# is implemented (e.g. when only the money/triage modules exist yet).

# A fixed, timezone-aware reference instant used across the suite. Chosen to be
# unambiguous (UTC) and stable so deadline/urgency assertions are exact.
FROZEN_INSTANT: _dt.datetime = _dt.datetime(2026, 6, 7, 12, 0, 0, tzinfo=_dt.timezone.utc)

# The date component of :data:`FROZEN_INSTANT`, convenient for SOL math that
# operates on calendar dates rather than instants.
FROZEN_DATE: _dt.date = FROZEN_INSTANT.date()


@pytest.fixture()
def frozen_now() -> _dt.datetime:
    """Return the fixed timezone-aware "now" instant for the test."""
    return FROZEN_INSTANT


@pytest.fixture()
def frozen_date() -> _dt.date:
    """Return the fixed "today" calendar date for SOL-deadline math."""
    return FROZEN_DATE


@pytest.fixture()
def frozen_clock(frozen_now: _dt.datetime) -> Callable[[], _dt.datetime]:
    """Return a zero-argument clock callable yielding the frozen instant.

    Pass this anywhere production code expects a ``now()`` provider; it makes
    time deterministic without monkeypatching :mod:`datetime`.
    """

    def _clock() -> _dt.datetime:
        return frozen_now

    return _clock


@pytest.fixture()
def fake_id_gen() -> Callable[[], str]:
    """Return a deterministic, monotonic id factory.

    Each call yields ``"id-0001"``, ``"id-0002"``, ... so generated entity
    ids are stable and ordered within a single test.
    """
    counter: Iterator[int] = itertools.count(1)

    def _gen() -> str:
        return f"id-{next(counter):04d}"

    return _gen


@pytest.fixture()
def prefixed_id_gen() -> Callable[[str], str]:
    """Return a deterministic id factory that accepts a per-kind prefix.

    Counts are shared across prefixes, so ``mk("appeal")`` then ``mk("event")``
    yield ``"appeal-0001"`` then ``"event-0002"`` -- monotonic and unique.
    """
    counter: Iterator[int] = itertools.count(1)

    def _gen(prefix: str) -> str:
        return f"{prefix}-{next(counter):04d}"

    return _gen


@pytest.fixture()
def denied_appeal_factory() -> Callable[[], object]:
    """Return a factory that builds a minimal ``Appeal`` in ``DENIED`` state.

    The factory constructs the smallest valid domain entity graph -- a
    :class:`Payer`, a :class:`Denial` and the wrapping :class:`Appeal` -- so
    aggregate/state-machine tests can start from a real ``DENIED`` appeal
    without each test re-deriving the construction. Domain modules are imported
    lazily so the rest of this conftest stays usable before they exist.
    """

    def _make() -> object:
        from backstop.domain.enums import AppealStatus
        from backstop.domain.models import Appeal, Denial, Payer
        from backstop.domain.money import Money, RecoverableDollars, SolDeadline

        payer = Payer(payer_id="payer-1", name="Acme Health")
        denial = Denial(
            denial_id="denial-1",
            payer=payer,
            plan="PPO",
            member_id_hash="hash-member",
            claim_number_hash="hash-claim",
            denial_code="197",
            rarc=None,
            cpt=("99213",),
            billed=Money(cents=50_000),
            dos=FROZEN_DATE,
            npis=("1234567890",),
        )
        return Appeal(
            id="appeal-1",
            status=AppealStatus.DENIED,
            denial=denial,
            recoverable=RecoverableDollars(Money(cents=50_000)),
            sol=SolDeadline(deadline=_dt.date(2026, 7, 7)),
            route=None,
            needs_human_review=False,
            events=(),
        )

    return _make

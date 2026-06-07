"""Unit tests for :mod:`backstop.domain.appeal_aggregate`.

The aggregate is a pure state machine: ``apply(appeal, event) -> appeal``
advances the :class:`AppealStatus` while enforcing the legal status graph and
appending the event to history. No I/O.

The load-bearing invariant under test: **an appeal cannot reach ``FILED``
without a :class:`SignedEvent` in its history.** Attempting to file an unsigned
appeal must raise an :class:`AppealStateError` (a :class:`BackstopError`).

The tests drive the machine through its happy path

    DENIED -> TRIAGED -> IN_APPEAL -> AWAITING_SIGNOFF -> (signed) -> FILED

and assert that illegal jumps and the unsigned-file shortcut are rejected.
"""

from __future__ import annotations

import datetime as _dt

import pytest

from backstop.domain.appeal_aggregate import apply
from backstop.domain.enums import AppealStatus
from backstop.domain.errors import AppealStateError, BackstopError
from backstop.domain.models import Appeal, AppealEvent, SignedEvent

_AT: _dt.date = _dt.date(2026, 6, 7)


# --------------------------------------------------------------------------- #
# Event builders (seq is auto-stamped by ``apply``; 0 is a safe placeholder).
# --------------------------------------------------------------------------- #
def _event(kind: str) -> AppealEvent:
    """Build a plain :class:`AppealEvent` of the given ``kind``."""
    return AppealEvent(kind=kind, seq=0, at=_AT)


def _signed() -> SignedEvent:
    """Build a :class:`SignedEvent` carrying a nurse signature."""
    return SignedEvent(
        kind="signed",
        seq=0,
        at=_AT,
        nurse_id="nurse-1",
        letter_sha256="a" * 64,
        signature_b64="c2lnbmF0dXJl",
    )


def _drive(appeal: Appeal, *events: AppealEvent) -> Appeal:
    """Fold a sequence of events through ``apply`` starting at *appeal*."""
    current = appeal
    for event in events:
        current = apply(current, event)
    return current


@pytest.fixture()
def denied_appeal(denied_appeal_factory: object) -> Appeal:
    """A freshly-opened appeal in the ``DENIED`` state."""
    return denied_appeal_factory()  # type: ignore[operator,no-any-return]


# --------------------------------------------------------------------------- #
# Happy path.
# --------------------------------------------------------------------------- #
def test_legal_path_denied_to_filed(denied_appeal: Appeal) -> None:
    """The full legal path reaches ``FILED`` once a signature is present."""
    filed = _drive(
        denied_appeal,
        _event("triaged"),
        _event("appeal_opened"),
        _event("letter_drafted"),
        _signed(),
    )
    assert filed.status is AppealStatus.FILED


def test_signed_event_recorded_in_history(denied_appeal: Appeal) -> None:
    """The signature event is retained in the appeal's event history."""
    filed = _drive(
        denied_appeal,
        _event("triaged"),
        _event("appeal_opened"),
        _event("letter_drafted"),
        _signed(),
    )
    assert filed.has_signed_event
    assert any(isinstance(event, SignedEvent) for event in filed.events)


def test_events_are_sequenced_monotonically(denied_appeal: Appeal) -> None:
    """``apply`` stamps a monotonic ``seq`` on each appended event."""
    appealed = _drive(denied_appeal, _event("triaged"), _event("appeal_opened"))
    assert [e.seq for e in appealed.events] == [0, 1]


# --------------------------------------------------------------------------- #
# The critical invariant: no FILED without a signature.
# --------------------------------------------------------------------------- #
def test_file_without_signature_event_raises(denied_appeal: Appeal) -> None:
    """A plain event that targets FILED from AWAITING_SIGNOFF must raise.

    No plain :class:`AppealEvent` can file an appeal; only a
    :class:`SignedEvent` is permitted to reach ``FILED``.
    """
    awaiting = _drive(
        denied_appeal,
        _event("triaged"),
        _event("appeal_opened"),
        _event("letter_drafted"),
    )
    assert awaiting.status is AppealStatus.AWAITING_SIGNOFF
    # There is no plain-event kind that targets FILED; a forged "signed"-kind
    # plain AppealEvent must still be refused because it is not a SignedEvent.
    with pytest.raises(AppealStateError):
        apply(awaiting, AppealEvent(kind="signed", seq=0, at=_AT))


def test_appeal_state_error_is_a_backstop_error() -> None:
    """The guard raises a domain error rooted at ``BackstopError``."""
    assert issubclass(AppealStateError, BackstopError)


def test_sign_directly_from_denied_raises(denied_appeal: Appeal) -> None:
    """A signature is illegal until the appeal is awaiting sign-off."""
    with pytest.raises(AppealStateError):
        apply(denied_appeal, _signed())


# --------------------------------------------------------------------------- #
# Illegal transitions.
# --------------------------------------------------------------------------- #
def test_illegal_jump_denied_to_awaiting_signoff_raises(
    denied_appeal: Appeal,
) -> None:
    """Skipping triage/appeal stages is rejected."""
    with pytest.raises(AppealStateError):
        apply(denied_appeal, _event("letter_drafted"))


def test_unknown_event_kind_raises(denied_appeal: Appeal) -> None:
    """An event whose kind names no transition is rejected."""
    with pytest.raises(AppealStateError):
        apply(denied_appeal, _event("teleported"))


# --------------------------------------------------------------------------- #
# Abandonment from any non-terminal state + terminality.
# --------------------------------------------------------------------------- #
def test_abandon_from_denied(denied_appeal: Appeal) -> None:
    """A non-terminal appeal may be abandoned."""
    abandoned = apply(denied_appeal, _event("abandoned"))
    assert abandoned.status is AppealStatus.ABANDONED


def test_abandon_from_in_appeal(denied_appeal: Appeal) -> None:
    """Abandonment is legal mid-appeal."""
    in_appeal = _drive(denied_appeal, _event("triaged"), _event("appeal_opened"))
    abandoned = apply(in_appeal, _event("abandoned"))
    assert abandoned.status is AppealStatus.ABANDONED


def test_cannot_transition_out_of_filed(denied_appeal: Appeal) -> None:
    """``FILED`` is terminal -- no further transition (incl. abandon) is legal."""
    filed = _drive(
        denied_appeal,
        _event("triaged"),
        _event("appeal_opened"),
        _event("letter_drafted"),
        _signed(),
    )
    with pytest.raises(AppealStateError):
        apply(filed, _event("abandoned"))


def test_cannot_transition_out_of_abandoned(denied_appeal: Appeal) -> None:
    """``ABANDONED`` is terminal -- no further events are accepted."""
    abandoned = apply(denied_appeal, _event("abandoned"))
    with pytest.raises(AppealStateError):
        apply(abandoned, _event("triaged"))


# --------------------------------------------------------------------------- #
# Purity.
# --------------------------------------------------------------------------- #
def test_apply_returns_new_appeal_without_mutating_input(
    denied_appeal: Appeal,
) -> None:
    """``apply`` is pure: the input appeal is unchanged."""
    triaged = apply(denied_appeal, _event("triaged"))
    assert denied_appeal.status is AppealStatus.DENIED
    assert denied_appeal.events == ()
    assert triaged is not denied_appeal
    assert triaged.status is AppealStatus.TRIAGED

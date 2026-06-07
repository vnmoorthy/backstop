"""Tests for :class:`SignoffService` — the compliance filing gate.

An appeal can be filed only when BOTH hold:
  1. ``audit.verify_chain(appeal_id)`` is ``True``, and
  2. the supplied signature verifies over the letter hash.

Any failure refuses to file (the status stays ``AWAITING_SIGNOFF``). The CAS is
the only path to ``FILED``.
"""

from __future__ import annotations

from backstop.domain.enums import AppealStatus
from backstop.ports.signature_port import Signature
from backstop.services.signoff_service import SignoffRefusal, SignoffService
from tests.services.fakes import (
    FakeAudit,
    FakeClock,
    FakeSignature,
    MemoryAppealRepo,
    make_appeal,
)

_HASH = b"redacted-letter-hash"


def _good_sig() -> Signature:
    """A signature that the fake verifier accepts."""
    return Signature(
        signature_b64=FakeSignature.VALID,
        public_key_id="key-1",
        nurse_identity="nurse-1",
        signed_at_iso="2026-06-07T12:00:00+00:00",
    )


def _bad_sig() -> Signature:
    """A signature the fake verifier rejects."""
    return Signature(
        signature_b64="forged",
        public_key_id="key-1",
        nurse_identity="nurse-1",
        signed_at_iso="2026-06-07T12:00:00+00:00",
    )


async def _repo_awaiting() -> MemoryAppealRepo:
    """A repo with one appeal in ``AWAITING_SIGNOFF``."""
    repo = MemoryAppealRepo()
    await repo.save(make_appeal("appeal-1", status=AppealStatus.AWAITING_SIGNOFF))
    return repo


def _service(repo: MemoryAppealRepo, *, chain_intact: bool) -> SignoffService:
    """Build a signoff service with a configurable audit verdict."""
    return SignoffService(
        repo=repo,
        audit=FakeAudit(intact=chain_intact),
        signature=FakeSignature(),
        clock=FakeClock(),
    )


async def test_files_when_chain_intact_and_signature_valid() -> None:
    """Both gates pass → the appeal transitions to FILED via CAS."""
    repo = await _repo_awaiting()
    service = _service(repo, chain_intact=True)

    result = await service.sign_off(
        "appeal-1", appeal_hash=_HASH, signature=_good_sig(), seq=0
    )

    assert result.filed is True
    assert result.refusal is None
    appeal = await repo.load("appeal-1")
    assert appeal.status is AppealStatus.FILED
    # A filing event was appended.
    assert [e.kind for e in repo.events_for("appeal-1")] == ["filed"]


async def test_refuses_when_chain_broken() -> None:
    """A broken audit chain refuses to file even with a valid signature."""
    repo = await _repo_awaiting()
    service = _service(repo, chain_intact=False)

    result = await service.sign_off(
        "appeal-1", appeal_hash=_HASH, signature=_good_sig(), seq=0
    )

    assert result.filed is False
    assert result.refusal is SignoffRefusal.AUDIT_CHAIN_BROKEN
    appeal = await repo.load("appeal-1")
    assert appeal.status is AppealStatus.AWAITING_SIGNOFF
    assert repo.events_for("appeal-1") == []
    # The status CAS was never attempted.
    assert repo.cas_calls == []


async def test_refuses_when_signature_invalid() -> None:
    """A bad signature refuses to file even with an intact chain."""
    repo = await _repo_awaiting()
    service = _service(repo, chain_intact=True)

    result = await service.sign_off(
        "appeal-1", appeal_hash=_HASH, signature=_bad_sig(), seq=0
    )

    assert result.filed is False
    assert result.refusal is SignoffRefusal.BAD_SIGNATURE
    appeal = await repo.load("appeal-1")
    assert appeal.status is AppealStatus.AWAITING_SIGNOFF
    assert repo.cas_calls == []


async def test_refuses_on_stale_state() -> None:
    """If the appeal already moved on, the CAS loses and filing is refused."""
    repo = MemoryAppealRepo()
    # Already FILED → not in AWAITING_SIGNOFF, so the CAS cannot win.
    await repo.save(make_appeal("appeal-1", status=AppealStatus.FILED))
    service = _service(repo, chain_intact=True)

    result = await service.sign_off(
        "appeal-1", appeal_hash=_HASH, signature=_good_sig(), seq=0
    )

    assert result.filed is False
    assert result.refusal is SignoffRefusal.STALE_STATE
    assert repo.events_for("appeal-1") == []


async def test_both_checks_required_truth_table() -> None:
    """Only (intact chain AND valid signature) files; all others refuse."""
    cases = [
        (True, True, True),
        (True, False, False),
        (False, True, False),
        (False, False, False),
    ]
    for intact, sig_valid, expect_filed in cases:
        repo = await _repo_awaiting()
        service = _service(repo, chain_intact=intact)
        sig = _good_sig() if sig_valid else _bad_sig()
        result = await service.sign_off(
            "appeal-1", appeal_hash=_HASH, signature=sig, seq=0
        )
        assert result.filed is expect_filed
        final = (await repo.load("appeal-1")).status
        expected_status = (
            AppealStatus.FILED if expect_filed else AppealStatus.AWAITING_SIGNOFF
        )
        assert final is expected_status

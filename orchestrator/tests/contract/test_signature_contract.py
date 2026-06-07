"""Contract suite for :class:`SignaturePort` (genuine Ed25519 sign/verify).

The concrete adapter is asserted to honour the port. Load-bearing M13
assertions:

* a signature round-trips: ``verify(hash, sign(hash)) is True``;
* a tampered hash is rejected (``verify`` returns ``False``, never raises);
* a signature from a different key / wrong key-id is rejected;
* malformed signature material returns ``False`` rather than raising.

The signed message is always the SHA-256 of the redacted letter -- no PHI ever
reaches the port.
"""

from __future__ import annotations

import datetime as dt
import hashlib

import pytest

from backstop.adapters.signoff.ed25519_signature_adapter import Ed25519SignatureAdapter
from backstop.ports.signature_port import Signature, SignaturePort


class _FixedClock:
    """A clock pinned to a fixed instant for deterministic sign timestamps."""

    def now(self) -> dt.datetime:
        return dt.datetime(2026, 6, 7, 12, 0, 0, tzinfo=dt.timezone.utc)

    def monotonic(self) -> float:
        return 0.0


def _adapter(seed: int = 1) -> Ed25519SignatureAdapter:
    """Build an adapter with a deterministic 32-byte key seed."""
    return Ed25519SignatureAdapter(
        clock=_FixedClock(),
        public_key_id=f"backstop-ed25519-{seed}",
        private_key_seed=bytes([seed]) * 32,
    )


def _hash(payload: bytes) -> bytes:
    return hashlib.sha256(payload).digest()


def test_adapter_satisfies_the_port() -> None:
    """The concrete adapter is recognised as the runtime-checkable port."""
    assert isinstance(_adapter(), SignaturePort)


def test_sign_then_verify_round_trips() -> None:
    """A freshly produced signature verifies over the same hash."""
    adapter = _adapter()
    appeal_hash = _hash(b"redacted appeal letter")
    signature = adapter.sign(appeal_hash, "nurse-jane")
    assert isinstance(signature, Signature)
    assert signature.nurse_identity == "nurse-jane"
    assert signature.signed_at_iso == "2026-06-07T12:00:00+00:00"
    assert adapter.verify(appeal_hash, signature) is True


def test_verify_rejects_tampered_hash() -> None:
    """A single-bit change to the hash fails verification (never raises)."""
    adapter = _adapter()
    appeal_hash = _hash(b"original letter")
    signature = adapter.sign(appeal_hash, "nurse-jane")
    tampered = bytearray(appeal_hash)
    tampered[0] ^= 0x01
    assert adapter.verify(bytes(tampered), signature) is False


def test_verify_rejects_wrong_key() -> None:
    """A signature made by a different keypair is rejected."""
    signer = _adapter(seed=1)
    other = _adapter(seed=2)
    appeal_hash = _hash(b"letter")
    signature = signer.sign(appeal_hash, "nurse-jane")
    # Different key-id -> rejected outright.
    assert other.verify(appeal_hash, signature) is False


def test_verify_rejects_same_keyid_different_key_material() -> None:
    """Same key-id but different private key still fails the cryptographic check."""
    signer = Ed25519SignatureAdapter(
        clock=_FixedClock(),
        public_key_id="shared-id",
        private_key_seed=b"\x01" * 32,
    )
    verifier = Ed25519SignatureAdapter(
        clock=_FixedClock(),
        public_key_id="shared-id",
        private_key_seed=b"\x02" * 32,
    )
    appeal_hash = _hash(b"letter")
    signature = signer.sign(appeal_hash, "nurse-jane")
    assert verifier.verify(appeal_hash, signature) is False


def test_verify_rejects_malformed_signature() -> None:
    """Non-base64 / wrong-length signature material returns False, not an error."""
    adapter = _adapter()
    appeal_hash = _hash(b"letter")
    template = adapter.sign(appeal_hash, "nurse-jane")
    bad = Signature(
        signature_b64="not-valid-base64-!!!",
        public_key_id=template.public_key_id,
        nurse_identity="nurse-jane",
        signed_at_iso=template.signed_at_iso,
    )
    assert adapter.verify(appeal_hash, bad) is False


def test_signature_is_deterministic_for_fixed_key() -> None:
    """Ed25519 signatures over the same hash+key are stable (RFC 8032)."""
    adapter = _adapter()
    appeal_hash = _hash(b"letter")
    first = adapter.sign(appeal_hash, "nurse-jane")
    second = adapter.sign(appeal_hash, "nurse-jane")
    assert first.signature_b64 == second.signature_b64


def test_bad_seed_length_is_rejected() -> None:
    """A non-32-byte private-key seed is refused at construction."""
    with pytest.raises(ValueError, match="32 bytes"):
        Ed25519SignatureAdapter(clock=_FixedClock(), private_key_seed=b"short")

"""Ed25519 sign/verify adapter for the mandatory human sign-off gate.

Implements :class:`backstop.ports.signature_port.SignaturePort` with a genuine
Ed25519 keypair (no stub, no echo). The gate signs the SHA-256 of the redacted
appeal letter with a nurse's identity; the resulting
:class:`~backstop.ports.signature_port.Signature` is verified before an appeal
may transition to ``FILED``. The hash is the only input -- raw PHI never reaches
this adapter.

The ``cryptography`` vendor library is imported lazily *inside* methods so this
module imports cleanly even when the SDK is absent; only ``sign``/``verify``
require it.
"""

from __future__ import annotations

import base64
from datetime import timezone
from typing import TYPE_CHECKING, Optional

from backstop.ports.clock_port import ClockPort
from backstop.ports.signature_port import Signature

if TYPE_CHECKING:  # pragma: no cover - typing only
    from cryptography.hazmat.primitives.asymmetric.ed25519 import (
        Ed25519PrivateKey,
        Ed25519PublicKey,
    )

__all__ = ["Ed25519SignatureAdapter"]


class Ed25519SignatureAdapter:
    """Genuine Ed25519 :class:`~backstop.ports.signature_port.SignaturePort`.

    Holds one Ed25519 keypair identified by ``public_key_id``. ``sign`` produces
    a detached, base64-encoded signature over the letter-hash bytes; ``verify``
    returns ``True`` only for an untampered hash signed by *this* key, and never
    raises on a mismatch.

    A 32-byte private-key seed may be injected for deterministic / reproducible
    keys (e.g. loaded from config); when omitted a fresh keypair is generated.
    """

    def __init__(
        self,
        *,
        clock: ClockPort,
        public_key_id: str = "backstop-ed25519",
        private_key_seed: Optional[bytes] = None,
    ) -> None:
        """Build the adapter, generating or loading the Ed25519 keypair.

        Args:
            clock: Injected clock supplying the ISO sign timestamp.
            public_key_id: Stable identifier recorded on every signature.
            private_key_seed: Optional 32-byte seed for a deterministic key.
        """
        from cryptography.hazmat.primitives.asymmetric.ed25519 import (
            Ed25519PrivateKey,
        )

        self._clock = clock
        self._public_key_id = public_key_id
        if private_key_seed is None:
            self._private_key: Ed25519PrivateKey = Ed25519PrivateKey.generate()
        else:
            if len(private_key_seed) != 32:
                raise ValueError("Ed25519 private-key seed must be exactly 32 bytes")
            self._private_key = Ed25519PrivateKey.from_private_bytes(private_key_seed)
        self._public_key: Ed25519PublicKey = self._private_key.public_key()

    def sign(self, appeal_hash: bytes, nurse_identity: str) -> Signature:
        """Sign ``appeal_hash`` on behalf of ``nurse_identity``.

        ``appeal_hash`` is the SHA-256 digest (32 bytes) of the redacted appeal
        letter. Returns a detached :class:`Signature`; no PHI is accepted or
        returned.
        """
        raw = self._private_key.sign(appeal_hash)
        return Signature(
            signature_b64=base64.b64encode(raw).decode("ascii"),
            public_key_id=self._public_key_id,
            nurse_identity=nurse_identity,
            signed_at_iso=self._now_iso(),
        )

    def verify(self, appeal_hash: bytes, signature: Signature) -> bool:
        """Return ``True`` iff ``signature`` is valid over ``appeal_hash``.

        Returns ``False`` for a tampered hash, a signature from the wrong key, a
        wrong ``public_key_id``, or any malformed input. Never raises on a
        verification mismatch.
        """
        from cryptography.exceptions import InvalidSignature

        if signature.public_key_id != self._public_key_id:
            return False
        try:
            raw = base64.b64decode(signature.signature_b64, validate=True)
        except (ValueError, TypeError):
            return False
        try:
            self._public_key.verify(raw, appeal_hash)
        except InvalidSignature:
            return False
        return True

    def _now_iso(self) -> str:
        """Return the clock's current instant as an ISO-8601 string."""
        moment = self._clock.now()
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=timezone.utc)
        return moment.isoformat()

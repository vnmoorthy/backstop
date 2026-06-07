"""Signature port: cryptographic sign-off over the redacted appeal-letter hash.

Defines the :class:`SignaturePort` protocol plus its signature DTO. The sign-off
gate signs the SHA-256 of the redacted appeal letter with a nurse's identity;
the resulting :class:`Signature` is verified before an appeal may transition to
``FILED``. The hash is the only input -- raw PHI never reaches this port.

Implemented by ``Ed25519SignatureAdapter`` (genuine Ed25519 via ``cryptography``).
This module imports only :mod:`backstop.domain`; it performs no I/O and imports
no vendor SDK.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class Signature:
    """A detached cryptographic signature over an appeal-letter hash.

    ``signature_b64`` is the base64-encoded detached signature; ``public_key_id``
    identifies the verifying key; ``nurse_identity`` records who signed; and
    ``signed_at_iso`` is the ISO-8601 signing timestamp. The signed message is
    always the redacted letter hash, never the letter contents.
    """

    signature_b64: str
    public_key_id: str
    nurse_identity: str
    signed_at_iso: str


@runtime_checkable
class SignaturePort(Protocol):
    """Sign/verify port for the mandatory human sign-off gate.

    Both methods operate on the appeal-letter hash bytes only. Services name this
    protocol and never the concrete adapter.
    """

    def sign(self, appeal_hash: bytes, nurse_identity: str) -> Signature:
        """Sign ``appeal_hash`` on behalf of ``nurse_identity``.

        ``appeal_hash`` is the SHA-256 digest of the redacted appeal letter.
        Returns a detached :class:`Signature`. No PHI is accepted or returned.
        """
        ...

    def verify(self, appeal_hash: bytes, signature: Signature) -> bool:
        """Return ``True`` iff ``signature`` is a valid signature over ``appeal_hash``.

        Returns ``False`` for a tampered hash, a signature from the wrong key, or
        any malformed input -- never raises on a verification mismatch.
        """
        ...

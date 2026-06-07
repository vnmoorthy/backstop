"""Sign-off adapters: cryptographic signature over the appeal-letter hash.

Houses :class:`~backstop.adapters.signoff.ed25519_signature_adapter.Ed25519SignatureAdapter`,
which implements :class:`backstop.ports.signature_port.SignaturePort` with a
genuine Ed25519 keypair from the ``cryptography`` library.
"""

from __future__ import annotations

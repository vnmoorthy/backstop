"""UUID4 identifier generator for :class:`IdGenPort`.

The single sanctioned source of new identifiers in production. Services mint
appeal ids, call ids, artifact refs and audit ids through this injected
singleton rather than calling :mod:`uuid`/:mod:`random` directly, so tests can
substitute a deterministic counter. This module imports only the standard
library and performs no I/O.
"""

from __future__ import annotations

import uuid

__all__ = ["UuidIdGenAdapter"]


class UuidIdGenAdapter:
    """Production :class:`~backstop.ports.id_gen_port.IdGenPort` implementation.

    Returns a fresh ``uuid4`` hex-dashed string on every call; the probability
    of a collision within a process is negligible.
    """

    def new_id(self) -> str:
        """Return a fresh, unique uuid4 string identifier."""
        return str(uuid.uuid4())

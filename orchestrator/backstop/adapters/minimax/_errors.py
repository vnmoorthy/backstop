"""Typed MiniMax adapter errors, all rooted in the domain hierarchy.

The real adapter translates every vendor/transport fault into one of these so
the Service layer can catch :class:`~backstop.domain.errors.BackstopError` at
the boundary (and degrade to the sim adapter or an on-device line) while still
pattern-matching the specific failure mode if it wants to.

Three failure modes are distinguished, mirroring the sponsor contract:

* :class:`MiniMaxTransportError` — the call never produced a usable HTTP
  response (timeout, connection error, non-2xx after bounded retries).
* :class:`MiniMaxApiError` — HTTP succeeded but MiniMax's native ``base_resp``
  envelope reported a non-zero ``status_code`` (a 200 can still be an error).
* :class:`MiniMaxParseError` — the response was well-formed HTTP but its body
  could not be parsed into the shape the adapter expects.

These are pure exception types: no I/O, no logging, no vendor imports.
"""

from __future__ import annotations

from typing import Optional

from backstop.domain.errors import BackstopError

__all__ = [
    "MiniMaxError",
    "MiniMaxTransportError",
    "MiniMaxApiError",
    "MiniMaxParseError",
]


class MiniMaxError(BackstopError):
    """Base class for every MiniMax reasoning-adapter failure."""


class MiniMaxTransportError(MiniMaxError):
    """Raised when the MiniMax endpoint could not be reached or returned non-2xx.

    Carries the HTTP status code when one was received (``None`` for a pure
    connection/timeout fault), never any response body, so logging it can leak
    no PHI.
    """

    def __init__(self, message: str, *, status: Optional[int] = None) -> None:
        """Record a PHI-free message and the optional HTTP status code."""
        super().__init__(message)
        self.status = status


class MiniMaxApiError(MiniMaxError):
    """Raised when MiniMax's native ``base_resp.status_code`` is non-zero.

    The native route can return HTTP 200 while signalling a logical error in
    ``base_resp``; the adapter must trust ``base_resp.status_code`` over the
    HTTP status. Carries the vendor status code and message (which is a service
    status string, not call content).
    """

    def __init__(self, status_code: int, status_msg: str) -> None:
        """Record the vendor ``status_code`` / ``status_msg`` pair."""
        super().__init__(f"minimax base_resp error {status_code}: {status_msg}")
        self.status_code = status_code
        self.status_msg = status_msg


class MiniMaxParseError(MiniMaxError):
    """Raised when a 2xx MiniMax response body cannot be parsed as expected."""

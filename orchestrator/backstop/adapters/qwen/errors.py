"""Typed synthesis error for the Qwen :class:`SpeechSynthesisPort` adapters.

The :class:`SpeechSynthesisPort` Protocol documents that both adapters raise
``SynthError`` on auth / rate-limit / expired-url / malformed-audio / timeout
failures. The shared domain :mod:`backstop.domain.errors` hierarchy does not
carry a synthesis-specific type, so it is defined here — at the adapter
boundary that owns vendor I/O — while still descending from
:class:`~backstop.domain.errors.BackstopError`. That keeps the honesty contract
intact: a caller catching ``BackstopError`` at a boundary still catches every
synthesis failure, and no raw ``httpx``/vendor exception is ever allowed to
escape across the port.

This module is pure: it imports only the standard library and the domain error
root. It performs no I/O and never logs (text is never logged on this path).
"""

from __future__ import annotations

from enum import Enum
from typing import Optional

from backstop.domain.errors import BackstopError

__all__ = ["SynthErrorKind", "SynthError"]


class SynthErrorKind(str, Enum):
    """Normalized category of a synthesis failure at the port boundary.

    Mirrors the failure taxonomy named in the ``SpeechSynthesisPort`` docstring
    so callers can branch on a stable, vendor-free reason instead of parsing an
    upstream status code.
    """

    AUTH = "auth"
    RATE_LIMIT = "rate_limit"
    EXPIRED_URL = "expired_url"
    MALFORMED_AUDIO = "malformed_audio"
    TIMEOUT = "timeout"
    BACKEND = "backend"


class SynthError(BackstopError):
    """Raised when speech synthesis fails at the adapter boundary.

    Descends from :class:`~backstop.domain.errors.BackstopError` so it is part
    of the domain failure surface: every vendor/runtime fault is translated into
    this type before it crosses the port, and no PHI or raw vendor exception
    text is carried.
    """

    def __init__(
        self,
        kind: SynthErrorKind,
        message: str,
        *,
        retry_after: Optional[float] = None,
    ) -> None:
        """Build the error with a normalized ``kind`` and a safe message.

        Args:
            kind: The normalized failure category.
            message: A short, PHI-free human-readable detail.
            retry_after: Seconds to wait before retrying, parsed from a vendor
                ``Retry-After`` header on rate-limit responses (if present).
        """
        super().__init__(f"{kind.value}: {message}")
        self.kind = kind
        self.retry_after = retry_after

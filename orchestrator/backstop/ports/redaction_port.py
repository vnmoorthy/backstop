"""RedactionPort — TrueFoundry PHI redaction boundary (L2 port).

The sole producer of ``RedactedText``: raw ``str`` enters here and only here is it
sanctioned into the egress-safe newtype. This makes unredacted PHI a type error at
every downstream egress port. Implementations perform real presidio-style
regex+context scrubbing and expose a ``contains_phi`` predicate for defence-in-depth
runtime assertions in egress adapters.

This module defines the Protocol plus its DTOs only; concrete adapters live in
``backstop.adapters.truefoundry``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Protocol, runtime_checkable

from backstop.domain.redacted import RedactedText

__all__ = [
    "Message",
    "RedactedMessage",
    "RedactionPort",
]


@dataclass(frozen=True)
class Message:
    """A raw, possibly-PHI-bearing chat message entering the boundary.

    Attributes:
        role: The OpenAI-style role (``system`` / ``user`` / ``assistant``).
        content: The raw message body that must be redacted before egress.
    """

    role: str
    content: str


@dataclass(frozen=True)
class RedactedMessage:
    """A chat message whose content has been sanctioned to ``RedactedText``.

    Attributes:
        role: The OpenAI-style role (``system`` / ``user`` / ``assistant``).
        content: The redacted, egress-safe message body.
    """

    role: str
    content: RedactedText


@runtime_checkable
class RedactionPort(Protocol):
    """The sole producer of ``RedactedText`` and the PHI egress gatekeeper."""

    def redact_text(self, text: str) -> RedactedText:
        """Scrub PHI from ``text`` and return the sanctioned ``RedactedText``."""
        ...

    def redact_messages(self, msgs: List[Message]) -> List[RedactedMessage]:
        """Redact every message in ``msgs``, preserving order and roles."""
        ...

    def contains_phi(self, text: str) -> bool:
        """Return whether ``text`` still matches any PHI pattern (defence-in-depth)."""
        ...

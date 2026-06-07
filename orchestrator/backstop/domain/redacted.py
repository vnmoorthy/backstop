"""PHI-as-a-type primitives for the Backstop appeals kernel.

The central idea is structural: :class:`RedactedText` is a newtype that can be
produced **only** through :meth:`RedactedText.from_redaction`. A direct
``RedactedText("...")`` from a raw string is refused at runtime. Because every
egress port in the system types its PHI-bearing fields as ``RedactedText``,
handing unredacted text to a boundary becomes a *type error* at author time and
a guarded *runtime error* if someone tries to forge the value.

This module is pure: it imports nothing outside the standard library, performs
no I/O, and does not itself detect PHI — detection/scrubbing lives in the
redaction adapter, which is the sole sanctioned caller of
:meth:`RedactedText.from_redaction`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Tuple

from backstop.domain.errors import RedactionError

__all__ = ["PhiTag", "PhiSpan", "RedactedText"]


# Internal sentinel proving the value flowed through the sanctioned factory.
# It is module-private and never exported, so callers outside this module cannot
# reconstruct a RedactedText without going through ``from_redaction``.
_REDACTION_SENTINEL: object = object()


class PhiTag(str, Enum):
    """Category of a piece of protected health information.

    Used to label the placeholder token left behind where PHI was removed, so
    downstream redacted text stays human-readable (e.g. ``[MEMBER_ID]``) while
    carrying no actual identifier.
    """

    MEMBER_ID = "MEMBER_ID"
    CLAIM_NUMBER = "CLAIM_NUMBER"
    NPI = "NPI"
    SSN = "SSN"
    DOB = "DOB"
    NAME = "NAME"
    ADDRESS = "ADDRESS"
    PHONE = "PHONE"
    EMAIL = "EMAIL"
    DATE = "DATE"
    OTHER = "OTHER"


@dataclass(frozen=True)
class PhiSpan:
    """A half-open ``[start, end)`` slice of source text identified as PHI.

    Spans are reported against the *original* (pre-redaction) string so an
    adapter can audit what it scrubbed. They carry positions and a category,
    never the removed substring itself.
    """

    start: int
    end: int
    tag: PhiTag

    def __post_init__(self) -> None:
        """Validate that the span is a well-formed, non-negative range."""
        if self.start < 0:
            raise RedactionError(f"PhiSpan.start must be >= 0, got {self.start}")
        if self.end < self.start:
            raise RedactionError(
                f"PhiSpan.end ({self.end}) must be >= start ({self.start})"
            )

    def __len__(self) -> int:
        """Return the character length of the span."""
        return self.end - self.start


@dataclass(frozen=True)
class RedactedText:
    """Immutable wrapper around text that has passed PHI redaction.

    The only sanctioned constructor is :meth:`from_redaction`. The generated
    ``__init__`` is fenced by an internal sentinel: any attempt to build a
    ``RedactedText`` directly from a raw string raises :class:`RedactionError`.
    """

    text: str
    spans: Tuple[PhiSpan, ...] = field(default_factory=tuple)
    _token: Any = None

    def __post_init__(self) -> None:
        """Refuse construction unless the internal sentinel was supplied.

        The dataclass ``__init__`` still exists (so ``from_redaction`` and
        pickling/copy round-trips can use it), but no external caller can pass
        the module-private sentinel, so the only legitimate path is the factory.
        """
        if self._token is not _REDACTION_SENTINEL:
            raise RedactionError(
                "RedactedText cannot be constructed from a raw string; "
                "use RedactedText.from_redaction(text, spans) via the "
                "redaction port"
            )
        # Drop the sentinel reference so equality/repr/hash ignore it and never
        # leak the marker object.
        object.__setattr__(self, "_token", None)

    @classmethod
    def from_redaction(
        cls,
        text: str,
        token: object,
        *,
        spans: Tuple[PhiSpan, ...] = (),
    ) -> RedactedText:
        """Build a :class:`RedactedText` from already-scrubbed ``text``.

        Args:
            text: The post-redaction string, with PHI replaced by placeholder
                tokens. The caller (the redaction adapter) is responsible for
                ensuring no PHI remains.
            token: The sanctioned-caller proof. Must be the module-private
                ``_REDACTION_SENTINEL`` exported to the adapter via
                :data:`SANCTIONED_TOKEN`; any other value is refused.
            spans: Optional audit record of the PHI positions removed from the
                source text.

        Returns:
            A frozen ``RedactedText`` instance.

        Raises:
            RedactionError: If ``token`` is not the sanctioned sentinel.
        """
        if token is not _REDACTION_SENTINEL:
            raise RedactionError(
                "from_redaction requires the sanctioned redaction token"
            )
        return cls(text=text, spans=tuple(spans), _token=_REDACTION_SENTINEL)

    def __str__(self) -> str:
        """Return the redacted (PHI-free) text."""
        return self.text

    def __len__(self) -> int:
        """Return the character length of the redacted text."""
        return len(self.text)


# The redaction adapter imports this name to mint RedactedText. It is the single
# capability that authorizes minting; keeping it here (rather than re-deriving
# ``object()`` elsewhere) is what makes the redaction port the *sole* producer.
SANCTIONED_TOKEN: object = _REDACTION_SENTINEL

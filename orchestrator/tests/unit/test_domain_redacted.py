"""Unit tests for :mod:`backstop.domain.redacted` -- the PHI boundary guard.

:class:`RedactedText` is the structural type every egress port requires for
PHI-bearing fields. Its only sanctioned constructor is the classmethod
``RedactedText.from_redaction(text, token)``; a raw ``RedactedText("...")`` from
a ``str`` must be blocked so that unredacted PHI cannot be smuggled past the
type system.

These are the lock tests for that boundary:

* constructing from a raw ``str`` (no sentinel) raises :class:`RedactionError`;
* a bogus token to the factory is refused;
* the sanctioned factory (driven via the exported ``SANCTIONED_TOKEN``) yields
  an instance whose text is preserved;
* equality is by value and the wrapper is frozen.
"""

from __future__ import annotations

import pytest

from backstop.domain.errors import RedactionError
from backstop.domain.redacted import (
    SANCTIONED_TOKEN,
    PhiSpan,
    PhiTag,
    RedactedText,
)


# --------------------------------------------------------------------------- #
# The guard: a raw str cannot become RedactedText.
# --------------------------------------------------------------------------- #
def test_cannot_construct_from_raw_str_via_init() -> None:
    """Calling ``RedactedText('...')`` directly must be rejected.

    The raw constructor is the smuggling path PHI would take; it must raise
    because the internal sentinel was not supplied.
    """
    with pytest.raises(RedactionError):
        RedactedText("Member John Doe MRN 12345")  # type: ignore[call-arg]


def test_cannot_construct_with_bogus_token() -> None:
    """Supplying a made-up token to the factory must not succeed."""
    with pytest.raises(RedactionError):
        RedactedText.from_redaction("redacted", token=object())


# --------------------------------------------------------------------------- #
# The sanctioned factory (driven via the exported producer token).
# --------------------------------------------------------------------------- #
def test_from_redaction_with_token_preserves_text() -> None:
    """The sanctioned factory yields a wrapper whose text is preserved."""
    redacted = RedactedText.from_redaction("[PATIENT] saw [PROVIDER]", token=SANCTIONED_TOKEN)
    assert isinstance(redacted, RedactedText)
    assert redacted.text == "[PATIENT] saw [PROVIDER]"
    assert str(redacted) == "[PATIENT] saw [PROVIDER]"
    assert len(redacted) == len("[PATIENT] saw [PROVIDER]")


def test_from_redaction_records_spans() -> None:
    """Optional audit spans are retained on the wrapper."""
    spans = (PhiSpan(start=0, end=9, tag=PhiTag.NAME),)
    redacted = RedactedText.from_redaction("[PATIENT] arrived", token=SANCTIONED_TOKEN, spans=spans)
    assert redacted.spans == spans


def test_equality_is_by_value() -> None:
    """Two redacted wrappers with the same text compare equal."""
    a = RedactedText.from_redaction("[X]", token=SANCTIONED_TOKEN)
    b = RedactedText.from_redaction("[X]", token=SANCTIONED_TOKEN)
    c = RedactedText.from_redaction("[Y]", token=SANCTIONED_TOKEN)
    assert a == b
    assert a != c


def test_sentinel_is_not_leaked_on_instance() -> None:
    """The construction sentinel is scrubbed so it never leaks via the value."""
    redacted = RedactedText.from_redaction("[X]", token=SANCTIONED_TOKEN)
    # ``_token`` is reset to ``None`` after construction (see __post_init__).
    assert redacted._token is None


def test_redacted_text_is_frozen() -> None:
    """The wrapper is immutable; reassigning its text raises."""
    redacted = RedactedText.from_redaction("[X]", token=SANCTIONED_TOKEN)
    with pytest.raises((AttributeError, TypeError)):
        redacted.text = "leak"  # type: ignore[misc]


# --------------------------------------------------------------------------- #
# Supporting PHI span/tag types.
# --------------------------------------------------------------------------- #
def test_phi_span_carries_offsets_and_tag() -> None:
    """``PhiSpan`` records a half-open ``[start, end)`` range with a tag."""
    span = PhiSpan(start=4, end=12, tag=PhiTag.MEMBER_ID)
    assert span.start == 4
    assert span.end == 12
    assert span.tag is PhiTag.MEMBER_ID
    assert len(span) == 8


def test_phi_span_rejects_inverted_range() -> None:
    """A span whose end precedes its start is invalid."""
    with pytest.raises(RedactionError):
        PhiSpan(start=10, end=4, tag=PhiTag.MEMBER_ID)


def test_phi_span_rejects_negative_start() -> None:
    """A negative start offset is invalid."""
    with pytest.raises(RedactionError):
        PhiSpan(start=-1, end=4, tag=PhiTag.MEMBER_ID)


def test_phi_span_is_frozen() -> None:
    """``PhiSpan`` is an immutable value object."""
    span = PhiSpan(start=0, end=1, tag=PhiTag.MEMBER_ID)
    with pytest.raises((AttributeError, TypeError)):
        span.start = 99  # type: ignore[misc]


def test_phi_tag_has_distinct_members() -> None:
    """``PhiTag`` exposes distinct, by-identity-comparable members."""
    members = list(PhiTag)
    assert len(members) == len(set(members))
    assert PhiTag.MEMBER_ID in members

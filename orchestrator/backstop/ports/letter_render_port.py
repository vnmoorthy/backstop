"""Letter render port: deterministic PDF rendering of a redacted appeal letter.

Defines the :class:`LetterRenderPort` protocol plus its input DTO. Rendering is
a PHI *egress* step (the bytes are persisted and downloaded), so every
free-text / PHI-bearing field of the input is structurally typed
:class:`backstop.domain.redacted.RedactedText`. Because ``RedactedText`` is
produced only by the redaction port, an unredacted letter is a type error at
this boundary -- the cleartext-PHI-in-PDF vulnerability cannot be expressed. The
adapter is additionally responsible for escaping all markup so a name containing
``<b>``/``<font>``/XML renders as literal text, not tags.

Implemented by ``ReportlabLetterAdapter`` (deterministic, markup-escaping). This
module imports only :mod:`backstop.domain`; it performs no I/O and imports no
vendor SDK.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, Tuple, runtime_checkable

from backstop.domain.money import Money, RecoverableDollars
from backstop.domain.redacted import RedactedText


@dataclass(frozen=True)
class RedactedAppealLetter:
    """The fully-redacted, render-ready appeal letter.

    Every free-text field is :class:`RedactedText`, so the letter can only have
    been assembled from redaction-port output. ``body_paragraphs`` carries the
    verbatim, audit-grade rebuttal prose; ``billed`` and ``recoverable`` are
    integer-cents money value objects (no float). All fields are escaped for
    markup safety by the renderer.

    Fields:
        payer_name: Redacted payer display name.
        recipient_block: Redacted addressee / appeals-desk block.
        claim_reference: Redacted claim reference line.
        denial_summary: Redacted one-line denial summary.
        body_paragraphs: Ordered redacted rebuttal paragraphs.
        billed: Original billed amount (integer cents).
        recoverable: Recoverable dollars asserted by the appeal.
        signoff_block: Redacted nurse sign-off / attestation block.
    """

    payer_name: RedactedText
    recipient_block: RedactedText
    claim_reference: RedactedText
    denial_summary: RedactedText
    body_paragraphs: Tuple[RedactedText, ...]
    billed: Money
    recoverable: RecoverableDollars
    signoff_block: RedactedText


@runtime_checkable
class LetterRenderPort(Protocol):
    """Render port turning a redacted appeal letter into deterministic PDF bytes.

    The single ``render`` method enforces the ``RedactedText``-only input.
    Implementations must be deterministic (identical input -> identical bytes)
    and must escape all markup. Services name this port and never the concrete
    adapter.
    """

    def render(self, letter: RedactedAppealLetter) -> bytes:
        """Render ``letter`` to PDF bytes.

        Accepts only a :class:`RedactedAppealLetter` whose free-text fields are
        :class:`RedactedText`; an unredacted letter is a type error at the call
        site. The output is deterministic and parseable, and every field is
        markup-escaped so embedded tags render as literal text.
        """
        ...

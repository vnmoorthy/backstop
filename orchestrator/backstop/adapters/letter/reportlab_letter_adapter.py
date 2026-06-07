"""Deterministic, markup-escaping reportlab PDF adapter for :class:`LetterRenderPort`.

Implements :class:`backstop.ports.letter_render_port.LetterRenderPort`. Rendering
is a PHI *egress* step, so the input
:class:`~backstop.ports.letter_render_port.RedactedAppealLetter` types every
free-text field as :class:`~backstop.domain.redacted.RedactedText` -- an
unredacted letter is a type error at the call site. This adapter is additionally
responsible for *escaping all markup*: a payer/recipient/body field containing
``<b>``/``<font>``/``&``/XML renders as literal text, never as reportlab
paragraph markup.

Two properties are load-bearing:

* **Markup safety** -- every string is passed through :func:`xml.sax.saxutils.escape`
  before it reaches a reportlab ``Paragraph``, so embedded tags cannot inject
  styling or break the document.
* **Determinism** -- the document is built in reportlab ``invariant`` mode, which
  pins the creation/modification timestamp and derives the document id from the
  content digest, so identical input yields byte-identical output (the contract
  test asserts this).

The ``reportlab`` library is imported lazily inside :meth:`render` so this module
imports cleanly even when the SDK is absent.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, List
from xml.sax.saxutils import escape

from backstop.ports.letter_render_port import LetterRenderPort, RedactedAppealLetter

if TYPE_CHECKING:  # pragma: no cover - typing only
    from io import BytesIO

    from reportlab.platypus import Flowable

__all__ = ["ReportlabLetterAdapter"]


class ReportlabLetterAdapter(LetterRenderPort):
    """Deterministic markup-escaping :class:`LetterRenderPort` implementation.

    Stateless: ``render`` builds the document entirely from its argument, so a
    single instance is safely shared process-wide.
    """

    def render(self, letter: RedactedAppealLetter) -> bytes:
        """Render ``letter`` to deterministic, markup-escaped PDF bytes.

        Every :class:`RedactedText` / money field is XML-escaped before it
        reaches a reportlab ``Paragraph``, so embedded tags render literally. The
        document is built in ``invariant`` mode, so identical input produces
        byte-identical output.
        """
        import io

        from reportlab.lib.pagesizes import LETTER
        from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
        from reportlab.lib.units import inch
        from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

        styles = getSampleStyleSheet()
        title_style = styles["Title"]
        heading_style = styles["Heading2"]
        body_style = ParagraphStyle(
            "RedactedBody",
            parent=styles["BodyText"],
            spaceAfter=8,
        )

        buffer: BytesIO = io.BytesIO()
        doc = SimpleDocTemplate(
            buffer,
            pagesize=LETTER,
            topMargin=0.9 * inch,
            bottomMargin=0.9 * inch,
            leftMargin=1.0 * inch,
            rightMargin=1.0 * inch,
            title="Appeal Letter",
            author="Backstop",
            subject="Claim Denial Appeal",
            creator="Backstop",
            # invariant mode pins the timestamp + document id -> deterministic bytes.
            invariant=True,
        )

        flow: List[Flowable] = [
            Paragraph(_safe(str(letter.payer_name)), title_style),
            Spacer(1, 12),
            Paragraph(_safe(str(letter.recipient_block)), body_style),
            Spacer(1, 8),
            Paragraph(_safe(str(letter.claim_reference)), heading_style),
            Paragraph(_safe(str(letter.denial_summary)), body_style),
            Spacer(1, 8),
        ]
        for paragraph in letter.body_paragraphs:
            flow.append(Paragraph(_safe(str(paragraph)), body_style))
        flow.extend(
            [
                Spacer(1, 8),
                Paragraph(_safe(f"Billed amount: {letter.billed.format()}"), body_style),
                Paragraph(
                    _safe(f"Amount recoverable: {letter.recoverable.format()}"),
                    body_style,
                ),
                Spacer(1, 16),
                Paragraph(_safe(str(letter.signoff_block)), body_style),
            ]
        )

        doc.build(flow)
        return buffer.getvalue()


def _safe(text: str) -> str:
    """Escape markup so embedded tags render as literal text.

    ``&``, ``<`` and ``>`` are all escaped, so a name like ``<font color=red>``
    appears verbatim in the PDF rather than being interpreted as paragraph
    markup. This is the renderer's markup-injection defence.
    """
    return escape(text)

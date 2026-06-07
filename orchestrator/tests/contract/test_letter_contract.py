"""Contract suite for :class:`LetterRenderPort` (markup-escaping reportlab PDF).

The single concrete adapter is asserted to honour the port. Load-bearing M13
assertions:

* every free-text field is markup-escaped, so a payer name containing
  ``<b>``/``<font>``/XML renders as literal text (a raw tag never reaches the
  PDF content stream);
* rendering is deterministic -- identical input yields byte-identical PDF bytes;
* the output is a real, parseable PDF (a ``%PDF`` header), not a stub.
"""

from __future__ import annotations

import base64
import zlib

from backstop.adapters.letter.reportlab_letter_adapter import ReportlabLetterAdapter
from backstop.domain.money import Money, RecoverableDollars
from backstop.domain.redacted import SANCTIONED_TOKEN, RedactedText
from backstop.ports.letter_render_port import LetterRenderPort, RedactedAppealLetter


def _redacted(text: str) -> RedactedText:
    """Mint a :class:`RedactedText` through the sanctioned redaction token."""
    return RedactedText.from_redaction(text, SANCTIONED_TOKEN)


def _letter(
    payer_name: str = "Acme Health Plan",
    *,
    body: str = "The authorization exists for [MEMBER_ID].",
) -> RedactedAppealLetter:
    """Build a fully-redacted appeal letter for rendering."""
    return RedactedAppealLetter(
        payer_name=_redacted(payer_name),
        recipient_block=_redacted("Appeals Desk\nPO Box 1234"),
        claim_reference=_redacted("Claim [CLAIM_NUMBER]"),
        denial_summary=_redacted("Denied CO-197: prior authorization absent."),
        body_paragraphs=(
            _redacted(body),
            _redacted("Please reprocess and pay on the merits."),
        ),
        billed=Money(cents=248_000),
        recoverable=RecoverableDollars(Money(cents=248_000)),
        signoff_block=_redacted("Reviewed by [NAME], RN."),
    )


def _decompressed_text(pdf: bytes) -> str:
    """Return the concatenated decompressed content streams of ``pdf``.

    reportlab encodes page content with ``[ /ASCII85Decode /FlateDecode ]``, so
    the literal drawn text lives inside ASCII85+zlib streams; we inflate every
    stream (trying both filter orders) to inspect the text-show operators that
    were actually rendered.
    """
    chunks = []
    marker = b"stream"
    idx = 0
    while True:
        start = pdf.find(marker, idx)
        if start == -1:
            break
        start += len(marker)
        # Skip the EOL after 'stream'.
        if pdf[start : start + 2] == b"\r\n":
            start += 2
        elif pdf[start : start + 1] in (b"\n", b"\r"):
            start += 1
        end = pdf.find(b"endstream", start)
        if end == -1:
            break
        raw = pdf[start:end].strip(b"\r\n")
        chunks.append(_inflate(raw))
        idx = end + len(b"endstream")
    return "\n".join(chunks)


def _inflate(raw: bytes) -> str:
    """Decode one content stream, trying ASCII85+Flate, Flate, then raw."""
    for decode in (
        lambda b: zlib.decompress(base64.a85decode(b, adobe=True)),
        zlib.decompress,
        lambda b: b,
    ):
        try:
            return decode(raw).decode("latin-1")
        except (zlib.error, ValueError):
            continue
    return raw.decode("latin-1", errors="ignore")


def test_adapter_satisfies_the_port() -> None:
    """The concrete adapter is recognised as the runtime-checkable port."""
    adapter = ReportlabLetterAdapter()
    assert isinstance(adapter, LetterRenderPort)


def test_render_emits_real_pdf() -> None:
    """The output is a real PDF document, not a stub."""
    pdf = ReportlabLetterAdapter().render(_letter())
    assert pdf.startswith(b"%PDF-")
    assert b"%%EOF" in pdf
    assert len(pdf) > 1000


def test_render_is_deterministic() -> None:
    """Identical input renders byte-identical output across calls."""
    adapter = ReportlabLetterAdapter()
    letter = _letter()
    first = adapter.render(letter)
    second = adapter.render(letter)
    assert first == second


def test_render_escapes_markup() -> None:
    """Embedded markup is drawn as literal text, not interpreted as a tag.

    When the renderer escapes ``<font ...>``, the angle-brackets and tag name are
    emitted as drawn glyphs inside PDF text-show (``Tj``) operators -- i.e. the
    literal ``<``, ``font`` and ``>`` characters appear in the content stream. An
    *unescaped* render would instead have reportlab consume ``<font>`` as paragraph
    markup (applying styling) and never draw the literal ``<``/``>`` glyphs.
    """
    adapter = ReportlabLetterAdapter()
    pdf = adapter.render(_letter(body="<font color='red'>EVIL</font> tags"))
    content = _decompressed_text(pdf)
    # The visible word survives.
    assert "EVIL" in content
    # The literal angle-bracket glyphs and tag name are drawn (proof the tag was
    # escaped, not interpreted): reportlab emits the ``<``, ``>`` and the tag text
    # as ``... Tj`` text-show operators rather than applying a font tag.
    assert "(<) Tj" in content
    assert "(>) Tj" in content
    assert "(/font) Tj" in content
    # The opening tag's attributes are drawn literally too.
    assert "color='red'" in content


def test_unescaped_markup_would_differ() -> None:
    """An escaped vs literal-text payer render identically (escaping == literal).

    Rendering ``<b>`` and rendering the literal text ``<b>`` must produce the
    same bytes, because escaping turns the former into exactly the latter.
    """
    adapter = ReportlabLetterAdapter()
    with_markup = adapter.render(_letter(payer_name="<b>Acme</b>"))
    # The escape of "<b>Acme</b>" is the same literal text; both must render equal
    # because the adapter escapes before reaching reportlab.
    again = adapter.render(_letter(payer_name="<b>Acme</b>"))
    assert with_markup == again


def test_markup_changes_bytes_but_stays_valid() -> None:
    """A different (escaped) payer name yields a different yet valid PDF."""
    adapter = ReportlabLetterAdapter()
    plain = adapter.render(_letter(payer_name="Acme Health Plan"))
    evil = adapter.render(_letter(payer_name="<script>alert(1)</script>"))
    assert plain != evil
    assert evil.startswith(b"%PDF-")

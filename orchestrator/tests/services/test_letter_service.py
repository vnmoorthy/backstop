"""Tests for :class:`LetterService` — redact strictly before render.

Pins that every free-text field is redacted before the renderer is invoked,
that the renderer only ever receives ``RedactedText``, and that the PDF bytes
are filed through the file-store port under an appeal-scoped ref.
"""

from __future__ import annotations

from typing import List, Optional

from backstop.domain.money import Money, RecoverableDollars
from backstop.domain.redacted import RedactedText
from backstop.ports.file_store_port import ArtifactRef, ArtifactScope
from backstop.ports.letter_render_port import RedactedAppealLetter
from backstop.services.letter_service import LetterDraft, LetterService
from tests.services.fakes import CallLog, FakeRedaction


class RecordingRenderer:
    """Letter-render fake recording when render runs and what it received."""

    def __init__(self, log: CallLog) -> None:
        """Store the shared ordering log; capture the last letter rendered."""
        self.log = log
        self.last: Optional[RedactedAppealLetter] = None

    def render(self, letter: RedactedAppealLetter) -> bytes:
        """Record the render call and return deterministic bytes."""
        self.log.append("render")
        self.last = letter
        return b"%PDF-1.4 fake"


class RecordingFileStore:
    """File-store fake recording put() calls and returning a scoped ref."""

    def __init__(self, log: CallLog) -> None:
        """Store the shared ordering log and the put-call record."""
        self.log = log
        self.puts: List[ArtifactScope] = []

    async def put(
        self,
        data: bytes,
        *,
        scope: ArtifactScope,
        ttl_seconds: int,
    ) -> ArtifactRef:
        """Record the put and return an opaque, scoped artifact ref."""
        self.log.append("put")
        self.puts.append(scope)
        return ArtifactRef(
            ref="ref-1",
            scope=scope,
            sha256="0" * 64,
            ttl_expires_at_iso="2026-06-08T12:00:00+00:00",
        )

    async def get_signed_url(self, ref: ArtifactRef, *, principal: object) -> object:
        """Unused by these tests."""
        raise NotImplementedError

    async def open(self, ref: ArtifactRef, *, principal: object) -> bytes:
        """Unused by these tests."""
        raise NotImplementedError

    async def delete(self, ref: ArtifactRef) -> None:
        """Unused by these tests."""

    async def sweep_expired(self) -> int:
        """Unused by these tests."""
        return 0


def _draft() -> LetterDraft:
    """Build a draft whose payer name carries a raw PHI token."""
    return LetterDraft(
        payer_name="Acme for MEMBER123",
        recipient_block="Appeals Desk",
        claim_reference="Claim MEMBER123",
        denial_summary="Denied CO-197",
        body_paragraphs=["We appeal for MEMBER123.", "Auth was on file."],
        billed=Money(cents=50_000),
        recoverable=RecoverableDollars(Money(cents=40_000)),
        signoff_block="Nurse R.",
    )


def _service(
    log: CallLog,
) -> tuple[LetterService, RecordingRenderer, RecordingFileStore, FakeRedaction]:
    """Wire a LetterService to recording fakes sharing one ordering log."""
    redaction = FakeRedaction(log)
    renderer = RecordingRenderer(log)
    files = RecordingFileStore(log)
    service = LetterService(redaction=redaction, renderer=renderer, files=files)
    return service, renderer, files, redaction


async def test_redacts_before_render() -> None:
    """All redactions happen strictly before the single render call."""
    log: CallLog = []
    service, renderer, _files, _red = _service(log)

    await service.render_and_store("appeal-1", _draft())

    assert "render" in log
    render_idx = log.index("render")
    # Every redact precedes render; render appears exactly once.
    assert log.count("render") == 1
    assert all(
        i < render_idx for i, marker in enumerate(log) if marker == "redact"
    )
    assert log.index("redact") < render_idx


async def test_renderer_receives_only_redacted_text() -> None:
    """Every free-text field the renderer sees is ``RedactedText``."""
    log: CallLog = []
    service, renderer, _files, _red = _service(log)

    await service.render_and_store("appeal-1", _draft())

    letter = renderer.last
    assert letter is not None
    assert isinstance(letter.payer_name, RedactedText)
    assert isinstance(letter.claim_reference, RedactedText)
    for para in letter.body_paragraphs:
        assert isinstance(para, RedactedText)
    # The raw PHI token never survives into the rendered fields.
    assert "MEMBER123" not in str(letter.payer_name)
    assert "MEMBER123" not in str(letter.claim_reference)


async def test_pdf_filed_under_appeal_scope() -> None:
    """The rendered PDF is filed last, scoped to the appeal."""
    log: CallLog = []
    service, _renderer, files, _red = _service(log)

    rendered = await service.render_and_store("appeal-1", _draft())

    assert log.index("put") > log.index("render")
    assert files.puts == [ArtifactScope(appeal_id="appeal-1", kind="appeal_letter")]
    assert rendered.ref.ref == "ref-1"
    assert rendered.pdf == b"%PDF-1.4 fake"
    # The returned hash is the SHA-256 of the rendered bytes.
    import hashlib

    assert rendered.sha256 == hashlib.sha256(b"%PDF-1.4 fake").hexdigest()

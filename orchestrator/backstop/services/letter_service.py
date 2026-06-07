"""LetterService — redact-then-render the appeal letter, then store it.

The load-bearing ordering, fixed here and asserted by the test: **every**
free-text field is passed through :class:`RedactionPort` *before* the
:class:`RedactedAppealLetter` is assembled and handed to
:class:`LetterRenderPort`. Because the renderer's input is structurally typed
``RedactedText``, an unredacted letter is a type error — but the service still
performs redaction first so no raw PHI is ever assembled into the render input.
The rendered PDF bytes are filed through :class:`FileStorePort` under a scoped,
TTL-bounded ref (never a path).

The service depends only on ports; it never constructs ``RedactedText`` itself.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Sequence, Tuple

from backstop.domain.money import Money, RecoverableDollars
from backstop.domain.redacted import RedactedText
from backstop.ports.file_store_port import ArtifactRef, ArtifactScope, FileStorePort
from backstop.ports.letter_render_port import (
    LetterRenderPort,
    RedactedAppealLetter,
)
from backstop.ports.redaction_port import RedactionPort

__all__ = ["LetterDraft", "RenderedLetter", "LetterService"]


@dataclass(frozen=True)
class LetterDraft:
    """The raw (possibly-PHI-bearing) letter content to redact then render.

    Every free-text field here is plain ``str`` — the service redacts each
    before it can reach the renderer.

    Attributes:
        payer_name: Raw payer display name.
        recipient_block: Raw addressee / appeals-desk block.
        claim_reference: Raw claim reference line.
        denial_summary: Raw one-line denial summary.
        body_paragraphs: Ordered raw rebuttal paragraphs.
        billed: Original billed amount (integer cents).
        recoverable: Recoverable dollars asserted by the appeal.
        signoff_block: Raw nurse sign-off / attestation block.
    """

    payer_name: str
    recipient_block: str
    claim_reference: str
    denial_summary: str
    body_paragraphs: Sequence[str]
    billed: Money
    recoverable: RecoverableDollars
    signoff_block: str


@dataclass(frozen=True)
class RenderedLetter:
    """A rendered, stored appeal letter.

    Attributes:
        ref: The scoped, TTL-bounded artifact ref the PDF was filed under.
        pdf: The rendered PDF bytes.
        sha256: The SHA-256 hex digest of ``pdf`` (the sign-off message).
    """

    ref: ArtifactRef
    pdf: bytes
    sha256: str


class LetterService:
    """Redact every field, render the letter, and file the PDF bytes."""

    def __init__(
        self,
        *,
        redaction: RedactionPort,
        renderer: LetterRenderPort,
        files: FileStorePort,
        ttl_seconds: int = 86_400,
    ) -> None:
        """Store the redaction, render, and file-store ports."""
        self._redaction = redaction
        self._renderer = renderer
        self._files = files
        self._ttl_seconds = ttl_seconds

    def redact(self, draft: LetterDraft) -> RedactedAppealLetter:
        """Redact every free-text field into a render-ready letter.

        This is the step the renderer's type signature depends on: the result's
        free-text fields are all ``RedactedText``.
        """
        body: Tuple[RedactedText, ...] = tuple(
            self._redaction.redact_text(p) for p in draft.body_paragraphs
        )
        return RedactedAppealLetter(
            payer_name=self._redaction.redact_text(draft.payer_name),
            recipient_block=self._redaction.redact_text(draft.recipient_block),
            claim_reference=self._redaction.redact_text(draft.claim_reference),
            denial_summary=self._redaction.redact_text(draft.denial_summary),
            body_paragraphs=body,
            billed=draft.billed,
            recoverable=draft.recoverable,
            signoff_block=self._redaction.redact_text(draft.signoff_block),
        )

    async def render_and_store(
        self,
        appeal_id: str,
        draft: LetterDraft,
    ) -> RenderedLetter:
        """Redact, render, and file the letter for ``appeal_id``.

        Redaction happens first; the renderer only ever sees ``RedactedText``.
        Returns the artifact ref plus the PDF and its hash (the sign-off input).
        """
        letter = self.redact(draft)
        pdf = self._renderer.render(letter)
        ref = await self._files.put(
            pdf,
            scope=ArtifactScope(appeal_id=appeal_id, kind="appeal_letter"),
            ttl_seconds=self._ttl_seconds,
        )
        return RenderedLetter(
            ref=ref,
            pdf=pdf,
            sha256=hashlib.sha256(pdf).hexdigest(),
        )

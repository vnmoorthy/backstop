"""ReviewService — nurse review worklist exposing redacted evidence only.

A nurse reviews an appeal before it can be signed. This service surfaces the
review queue (appeals flagged ``needs_human_review``) and, for one appeal,
exposes its supporting evidence — but every free-text body it returns is
:class:`RedactedText`, produced through :class:`RedactionPort`. Raw evidence
text is redacted on the way out, so a nurse-facing surface can never carry PHI.

The service performs no mutation of the aggregate; it reads through the
:class:`AppealRepositoryPort` and redacts evidence text through the redaction
port. It depends only on ports.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence, Tuple

from backstop.domain.models import Appeal
from backstop.domain.redacted import RedactedText
from backstop.ports.appeal_repository_port import (
    AppealFilter,
    AppealRepositoryPort,
)
from backstop.ports.redaction_port import RedactionPort

__all__ = ["ReviewEvidence", "ReviewPacket", "ReviewService"]


@dataclass(frozen=True)
class ReviewEvidence:
    """One redacted evidence row shown to the reviewing nurse.

    Attributes:
        chunk_id: Stable citation key for the evidence.
        source: Provenance label (non-PHI).
        body: The redacted passage text (egress-safe).
    """

    chunk_id: str
    source: str
    body: RedactedText


@dataclass(frozen=True)
class ReviewPacket:
    """Everything a nurse needs to review one appeal, fully redacted.

    Attributes:
        appeal_id: The appeal under review.
        evidence: The redacted evidence rows backing the rebuttal.
    """

    appeal_id: str
    evidence: Tuple[ReviewEvidence, ...]


@dataclass(frozen=True)
class RawEvidence:
    """A raw (pre-redaction) evidence row handed to the review service."""

    chunk_id: str
    source: str
    text: str


class ReviewService:
    """Build the nurse review queue and redacted per-appeal review packets."""

    def __init__(
        self,
        repo: AppealRepositoryPort,
        redaction: RedactionPort,
    ) -> None:
        """Store the repository and redaction ports."""
        self._repo = repo
        self._redaction = redaction

    async def queue(
        self,
        *,
        limit: int = 50,
        offset: int = 0,
    ) -> Tuple[Appeal, ...]:
        """Return the page of appeals flagged for human review."""
        page = await self._repo.list(
            AppealFilter(needs_human_review=True),
            limit=limit,
            offset=offset,
        )
        return tuple(page.items)

    def build_packet(
        self,
        appeal_id: str,
        evidence: Sequence[RawEvidence],
    ) -> ReviewPacket:
        """Redact every evidence body and return a nurse-facing review packet.

        No raw evidence text escapes: each row's body is redacted before it is
        placed on the returned packet.
        """
        rows: Tuple[ReviewEvidence, ...] = tuple(
            ReviewEvidence(
                chunk_id=item.chunk_id,
                source=item.source,
                body=self._redaction.redact_text(item.text),
            )
            for item in evidence
        )
        return ReviewPacket(appeal_id=appeal_id, evidence=rows)

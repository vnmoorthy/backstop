"""DenialParserPort — Unsiloed Stage-0 denial extraction (L2 port).

Turns a denial artifact (EOB / CMS-1500 / UB-04 images via real Unsiloed, or
835/837/277 EDI via the deterministic sim) into a structured ``DenialExtraction``
with per-field provenance, an overall confidence, and a ``needs_human_review``
flag driven by a confidence floor. Artifacts are passed as validated in-memory
bytes only — no code path opens a server-side path from user input, which kills
arbitrary-file-read at the source. The real adapter refuses raw EDI.

This module defines the Protocol plus its request/result DTOs only; concrete
adapters live in ``backstop.adapters.unsiloed``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Optional, Protocol, Tuple, runtime_checkable

from backstop.domain.enums import ArtifactKind

__all__ = [
    "ParseRequest",
    "ExtractedField",
    "DenialExtraction",
    "DenialParserPort",
]


@dataclass(frozen=True)
class ParseRequest:
    """A denial artifact to extract, carried as validated in-memory bytes.

    Attributes:
        content: The raw artifact bytes (never a server-side path).
        kind: The declared artifact kind, used for engine routing.
        filename: Optional original filename for provenance/logging only.
    """

    content: bytes
    kind: ArtifactKind
    filename: Optional[str] = None


@dataclass(frozen=True)
class ExtractedField:
    """One extracted field with its confidence and source provenance.

    Attributes:
        name: Canonical field name (e.g. ``claim_number``, ``carc_code``).
        value: The extracted string value.
        confidence: Per-field confidence in ``[0, 1]``.
        page_no: Source page number for image artifacts, when applicable.
        provenance: Free-form provenance (segment id, bbox, regex tag, ...).
    """

    name: str
    value: str
    confidence: float
    page_no: Optional[int] = None
    provenance: Dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class DenialExtraction:
    """The structured result of parsing one denial artifact.

    Attributes:
        kind: The artifact kind that was parsed.
        fields: Per-field extractions with provenance.
        overall_confidence: Aggregate confidence in ``[0, 1]``.
        needs_human_review: Whether confidence fell below the floor.
    """

    kind: ArtifactKind
    fields: Tuple[ExtractedField, ...]
    overall_confidence: float
    needs_human_review: bool


@runtime_checkable
class DenialParserPort(Protocol):
    """Async denial-artifact parser producing structured, provenanced fields."""

    async def parse(self, req: ParseRequest) -> DenialExtraction:
        """Parse the artifact in ``req`` into a structured extraction.

        Raises:
            UnsupportedArtifact: If this adapter cannot handle ``req.kind``.
        """
        ...

    def supports(self, kind: ArtifactKind) -> bool:
        """Return whether this adapter can parse ``kind``."""
        ...

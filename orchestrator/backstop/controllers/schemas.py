"""Pydantic v2 request/response DTOs for the controller edge.

Request DTOs validate and bound the wire input; response DTOs carry **only**
non-PHI / already-redacted fields. The domain entities hold PHI as salted hashes
and money as integer cents, and the response models never surface a raw member
id, claim number, or letter body — redacted text and opaque ids only.

These models are deliberately thin: the controllers map domain DTOs into them by
hand so there is one explicit place that decides what leaves the trust boundary.
"""

from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "DenialIn",
    "CreateAppealRequest",
    "AppealOut",
    "TriageItemOut",
    "TriageListOut",
    "ReviewEvidenceOut",
    "ReviewPacketOut",
    "ReviewQueueItemOut",
    "ReviewQueueOut",
    "SignoffRequest",
    "SignoffResponse",
    "SignedUrlOut",
    "IngestionResponse",
    "ErrorOut",
]


class _Strict(BaseModel):
    """Base model: forbid unknown fields so malformed input fails fast."""

    model_config = ConfigDict(extra="forbid")


class DenialIn(_Strict):
    """A synthetic denial submitted to open an appeal (PHI pre-hashed)."""

    payer_id: str = Field(min_length=1, max_length=64)
    payer_name: str = Field(min_length=1, max_length=200)
    plan: str = Field(min_length=1, max_length=64)
    member_id_hash: str = Field(min_length=1, max_length=128)
    claim_number_hash: str = Field(min_length=1, max_length=128)
    denial_code: str = Field(min_length=1, max_length=16)
    rarc: Optional[str] = Field(default=None, max_length=16)
    cpt: List[str] = Field(default_factory=list, max_length=64)
    billed_cents: int = Field(ge=0)
    dos: str = Field(description="Date of service, ISO-8601 (YYYY-MM-DD)")
    npis: List[str] = Field(default_factory=list, max_length=64)


class CreateAppealRequest(_Strict):
    """Open an appeal from a denial plus recoverable amount and SOL deadline."""

    denial: DenialIn
    recoverable_cents: int = Field(ge=0)
    sol_deadline: str = Field(description="Statute-of-limitations date, ISO-8601")
    needs_human_review: bool = False


class AppealOut(_Strict):
    """Redacted-only view of an appeal aggregate."""

    id: str
    status: str
    route: Optional[str] = None
    payer_id: str
    denial_code: str
    recoverable_cents: int
    sol_deadline: str
    needs_human_review: bool


class TriageItemOut(_Strict):
    """One ranked worklist row (score + redacted appeal view)."""

    appeal: AppealOut
    score: float


class TriageListOut(_Strict):
    """The ranked triage worklist."""

    items: List[TriageItemOut]


class ReviewEvidenceOut(_Strict):
    """One redacted evidence row shown to the reviewing nurse."""

    chunk_id: str
    source: str
    body: str  # already RedactedText -> str at the boundary


class ReviewPacketOut(_Strict):
    """A nurse-facing review packet (fully redacted bodies)."""

    appeal_id: str
    evidence: List[ReviewEvidenceOut]


class ReviewQueueItemOut(_Strict):
    """One appeal awaiting nurse review (redacted view)."""

    appeal: AppealOut


class ReviewQueueOut(_Strict):
    """The nurse review queue."""

    items: List[ReviewQueueItemOut]


class SignoffRequest(_Strict):
    """Submit a nurse sign-off over an appeal-letter hash."""

    appeal_hash_hex: str = Field(min_length=2, max_length=256)
    signature_b64: str = Field(min_length=1, max_length=4096)
    public_key_id: str = Field(min_length=1, max_length=128)
    nurse_identity: str = Field(min_length=1, max_length=128)
    signed_at_iso: str = Field(min_length=1, max_length=64)
    seq: int = Field(ge=0)


class SignoffResponse(_Strict):
    """The outcome of an attempted sign-off."""

    filed: bool
    appeal_id: str
    refusal: Optional[str] = None


class SignedUrlOut(_Strict):
    """A short-lived signed URL for an artifact."""

    url: str
    expires_at_iso: str


class IngestionResponse(_Strict):
    """The structured result of ingesting one denial artifact (non-PHI)."""

    appeal_id: str
    kind: str
    overall_confidence: float
    needs_human_review: bool
    used_fallback: bool
    field_names: List[str]


class ErrorOut(_Strict):
    """A safe, PHI-free error envelope."""

    detail: str

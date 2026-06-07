"""Review controller: nurse queue, evidence timeline, and sign-off.

* ``GET /review/queue`` — the nurse worklist (appeals flagged
  ``needs_human_review``), returned as redacted views.
* ``GET /review/{appeal_id}/evidence`` — the redacted evidence timeline backing
  the rebuttal (per-appeal ownership gated). Bodies are ``RedactedText``.
* ``POST /review/{appeal_id}/signoff`` — the compliance gate: an appeal reaches
  ``FILED`` only on an intact audit chain plus a valid Ed25519 signature over the
  redacted letter hash. Ownership-gated; admins/nurses with the ``sign`` grant.

Every route is authenticated and returns redacted-only bodies.
"""

from __future__ import annotations

import binascii
from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, HTTPException, Query, status

from backstop.controllers.appeals_controller import appeal_to_out
from backstop.controllers.dependencies import (
    get_auth_service,
    get_container,
    get_principal,
    require_authorized,
    require_worklist_reader,
)
from backstop.controllers.schemas import (
    ReviewEvidenceOut,
    ReviewPacketOut,
    ReviewQueueItemOut,
    ReviewQueueOut,
    SignoffRequest,
    SignoffResponse,
)
from backstop.domain.errors import AppealNotFound
from backstop.ports.auth_port import Principal
from backstop.ports.retrieval_port import RetrievalQuery
from backstop.ports.signature_port import Signature
from backstop.services.review_service import RawEvidence

if TYPE_CHECKING:  # pragma: no cover - typing only
    from backstop.composition.container import Container
    from backstop.services.auth_service import AuthService

router = APIRouter(prefix="/review", tags=["review"])


@router.get("/queue", response_model=ReviewQueueOut)
async def review_queue(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    principal: Principal = Depends(get_principal),  # noqa: B008
    container: Container = Depends(get_container),  # noqa: B008
) -> ReviewQueueOut:
    """Return the nurse review queue (authn + worklist-reader role)."""
    require_worklist_reader(principal)
    service = container.review_service
    assert service is not None  # noqa: S101
    appeals = await service.queue(limit=limit, offset=offset)
    return ReviewQueueOut(
        items=[ReviewQueueItemOut(appeal=appeal_to_out(a)) for a in appeals]
    )


@router.get("/{appeal_id}/evidence", response_model=ReviewPacketOut)
async def review_evidence(
    appeal_id: str,
    principal: Principal = Depends(get_principal),  # noqa: B008
    auth: AuthService = Depends(get_auth_service),  # noqa: B008
    container: Container = Depends(get_container),  # noqa: B008
) -> ReviewPacketOut:
    """Return the redacted evidence timeline for one appeal (ownership-gated)."""
    require_authorized(
        auth, principal, action="read", resource="events", resource_id=appeal_id
    )
    appeal_svc = container.appeal_service
    review_svc = container.review_service
    retrieval = container.retrieval
    assert appeal_svc is not None and review_svc is not None  # noqa: S101
    assert retrieval is not None  # noqa: S101

    try:
        appeal = await appeal_svc.get(appeal_id)
    except AppealNotFound as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="appeal not found"
        ) from exc

    # Retrieve rebuttal evidence from PHI-free denial context only.
    result = await retrieval.retrieve(
        RetrievalQuery(
            text=f"CARC {appeal.denial.denial_code} payer {appeal.denial.payer.payer_id}",
            carc=appeal.denial.denial_code,
            payer_id=appeal.denial.payer.payer_id,
        )
    )
    raw = [
        RawEvidence(chunk_id=c.chunk_id, source=c.source, text=c.text)
        for c in result.chunks
    ]
    packet = review_svc.build_packet(appeal_id, raw)
    return ReviewPacketOut(
        appeal_id=packet.appeal_id,
        evidence=[
            ReviewEvidenceOut(chunk_id=e.chunk_id, source=e.source, body=str(e.body))
            for e in packet.evidence
        ],
    )


@router.post("/{appeal_id}/signoff", response_model=SignoffResponse)
async def submit_signoff(
    appeal_id: str,
    body: SignoffRequest,
    principal: Principal = Depends(get_principal),  # noqa: B008
    auth: AuthService = Depends(get_auth_service),  # noqa: B008
    container: Container = Depends(get_container),  # noqa: B008
) -> SignoffResponse:
    """Submit a nurse sign-off; file the appeal only if both gates pass."""
    require_authorized(
        auth, principal, action="sign", resource="appeals", resource_id=appeal_id
    )
    service = container.signoff_service
    assert service is not None  # noqa: S101

    try:
        appeal_hash = binascii.unhexlify(body.appeal_hash_hex)
    except (binascii.Error, ValueError) as exc:
        raise HTTPException(
            status_code=422,
            detail="appeal_hash_hex must be valid hex",
        ) from exc

    result = await service.sign_off(
        appeal_id,
        appeal_hash=appeal_hash,
        signature=Signature(
            signature_b64=body.signature_b64,
            public_key_id=body.public_key_id,
            nurse_identity=body.nurse_identity,
            signed_at_iso=body.signed_at_iso,
        ),
        seq=body.seq,
    )
    return SignoffResponse(
        filed=result.filed,
        appeal_id=result.appeal_id,
        refusal=result.refusal.value if result.refusal is not None else None,
    )

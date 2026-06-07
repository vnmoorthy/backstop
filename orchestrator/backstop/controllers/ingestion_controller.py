"""Ingestion controller: validated, size-capped denial-artifact upload.

``POST /ingestion`` accepts a denial artifact as in-memory bytes, validates the
declared content type against an allowlist and the size against the configured
cap (closes the unbounded-upload / wrong-content-type findings), then parses it
through :class:`IngestDenialService` (gate-capped, audit-wrapped, EDI fallback).
No server-side path is ever derived from the upload — only validated bytes plus
a declared :class:`ArtifactKind` cross the boundary. Authn-gated; the response
carries non-PHI extraction metadata only.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Dict

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status

from backstop.controllers.dependencies import (
    get_auth_service,
    get_container,
    get_principal,
    require_authorized,
)
from backstop.controllers.schemas import IngestionResponse
from backstop.domain.enums import ArtifactKind
from backstop.ports.auth_port import Principal
from backstop.ports.denial_parser_port import ParseRequest

if TYPE_CHECKING:  # pragma: no cover - typing only
    from backstop.composition.container import Container
    from backstop.services.auth_service import AuthService

router = APIRouter(prefix="/ingestion", tags=["ingestion"])

# Content types we accept for a denial artifact. EDI is plain text; EOB/claim
# images are PDF/PNG/JPEG. Anything else is rejected before a byte is read into
# a parser (defence against type-confusion / oversized binaries).
_ALLOWED_CONTENT_TYPES: Dict[str, ArtifactKind] = {
    "application/pdf": ArtifactKind.PDF_IMAGE,
    "image/png": ArtifactKind.PDF_IMAGE,
    "image/jpeg": ArtifactKind.PDF_IMAGE,
    "text/plain": ArtifactKind.X12_835,
    "application/edi-x12": ArtifactKind.X12_835,
    "application/octet-stream": ArtifactKind.X12_835,
}


@router.post("", response_model=IngestionResponse, status_code=status.HTTP_201_CREATED)
async def ingest_artifact(
    appeal_id: str = Form(..., max_length=128),
    kind: str = Form(..., max_length=32),
    file: UploadFile = File(...),  # noqa: B008
    principal: Principal = Depends(get_principal),  # noqa: B008
    auth: AuthService = Depends(get_auth_service),  # noqa: B008
    container: Container = Depends(get_container),  # noqa: B008
) -> IngestionResponse:
    """Validate and ingest a denial artifact for ``appeal_id`` (authn + authz)."""
    require_authorized(
        auth, principal, action="update", resource="appeals", resource_id=appeal_id
    )

    settings = container.settings
    assert settings is not None  # noqa: S101
    max_bytes = settings.max_upload_bytes

    content_type = (file.content_type or "").split(";", 1)[0].strip().lower()
    if content_type not in _ALLOWED_CONTENT_TYPES:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=f"unsupported content type: {content_type or 'unknown'}",
        )

    try:
        artifact_kind = ArtifactKind(kind)
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail=f"unknown artifact kind: {kind}",
        ) from exc

    data = await _read_capped(file, max_bytes)

    service = container.ingest_service
    assert service is not None  # noqa: S101
    result = await service.ingest(
        appeal_id,
        ParseRequest(content=data, kind=artifact_kind, filename=file.filename),
    )
    extraction = result.extraction
    return IngestionResponse(
        appeal_id=appeal_id,
        kind=extraction.kind.value,
        overall_confidence=extraction.overall_confidence,
        needs_human_review=extraction.needs_human_review,
        used_fallback=result.used_fallback,
        field_names=[f.name for f in extraction.fields],
    )


async def _read_capped(file: UploadFile, max_bytes: int) -> bytes:
    """Read the upload, rejecting anything over ``max_bytes`` (streamed cap).

    Reads one byte past the cap so an exactly-at-cap payload is accepted while an
    over-cap one is rejected without buffering the whole oversized body.
    """
    data = await file.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"upload exceeds {max_bytes} bytes",
        )
    if not data:
        raise HTTPException(
            status_code=422,
            detail="empty upload",
        )
    return data

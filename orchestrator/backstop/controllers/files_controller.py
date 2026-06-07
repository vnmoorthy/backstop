"""Files controller: signed-URL artifact serving with per-appeal ownership.

Artifacts (rendered appeal-letter PDFs) are never served from a user-supplied
path and never served unauthenticated. Two routes:

* ``GET /files/{ref}`` — serves bytes **only** with a valid, unexpired signed
  ``token`` (and ``expires``) minted by the file store after an ownership check.
  This route is mounted at the application root (not under ``/v1``) so it matches
  the URL the store mints; the signed token is the capability, so no bearer
  header is required. A missing/forged/expired token is rejected.

The signed URL itself is produced by :meth:`FileStorePort.get_signed_url`, which
enforces per-appeal ownership against the requesting :class:`Principal`; this
controller only redeems that capability.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter, HTTPException, Query, Request, Response, status

from backstop.domain.errors import Forbidden

if TYPE_CHECKING:  # pragma: no cover - typing only
    from backstop.composition.container import Container

# Root-mounted (no ``/v1`` prefix) to match the store-minted ``/files/{ref}`` URL.
router = APIRouter(tags=["files"])


@router.get("/files/{ref}")
async def serve_file(
    request: Request,
    ref: str,
    expires: str = Query(..., description="ISO-8601 expiry bound into the token"),
    token: str = Query(..., min_length=1, description="HMAC signature over ref+expiry"),
) -> Response:
    """Serve an artifact's bytes iff the signed token is valid and unexpired."""
    container: Container = request.app.state.container
    store = container.files
    read_signed = getattr(store, "read_signed", None)
    if read_signed is None:  # pragma: no cover - real S3 serves presigned URLs itself
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="signed file serving is handled by the storage backend",
        )

    try:
        data = await read_signed(ref, expires, token)
    except Forbidden as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="invalid or expired file token",
        ) from exc
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="artifact not found"
        ) from exc

    return Response(
        content=data,
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{ref}.pdf"'},
    )

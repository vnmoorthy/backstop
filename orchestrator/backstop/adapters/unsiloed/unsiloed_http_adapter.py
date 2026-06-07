"""Unsiloed document-understanding adapter (REAL) — async extract + poll.

This is the ``real`` binding of :class:`~backstop.ports.denial_parser_port.\
DenialParserPort`. It drives the Unsiloed vision API over an injected
:class:`httpx.AsyncClient`:

1. **Refuse raw EDI.** ``X12_835`` / ``X12_837`` / ``X12_277`` raise
   :class:`UnsupportedArtifact` so the ingestion service falls back to the
   deterministic parser — EDI never hits the network.
2. **Create.** ``POST /v2/extract`` as ``multipart/form-data`` with the artifact
   bytes plus a stringified Denial JSON Schema (``schema_data``). The credential
   travels in the custom ``api-key`` header (NOT ``Bearer``).
3. **Poll.** ``GET /extract/{job_id}`` with capped exponential backoff until the
   status is terminal. Note the path asymmetry: create is ``/v2/extract`` but the
   poll path drops the ``v2``.
4. **Map.** The terminal body is a flat ``{field_name: {value, score, bboxes,
   page_no}}`` map plus a top-level ``min_confidence_score``; each field becomes
   an :class:`ExtractedField` and ``min_confidence_score`` becomes
   ``overall_confidence``. ``needs_human_review`` trips below the injected floor.

Security: the adapter only ever ships the in-memory bytes the controller already
validated. There is **no** code path that opens a server-side path from user
input — closing the audited "arbitrary file read in EOB parse" finding. The
``httpx`` SDK is imported lazily inside the request method so this module imports
cleanly even when ``httpx`` is absent; cross-cutting concerns (audit, redaction,
concurrency cap) are applied by the wrapping service, not here.
"""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

from backstop.adapters.unsiloed import _denial_schema as fields
from backstop.adapters.unsiloed.errors import (
    UnsiloedAuthError,
    UnsiloedJobFailed,
    UnsiloedTimeout,
    UnsupportedArtifact,
)
from backstop.domain.enums import ArtifactKind
from backstop.ports.denial_parser_port import (
    DenialExtraction,
    ExtractedField,
    ParseRequest,
)

if TYPE_CHECKING:  # pragma: no cover - typing only; httpx is imported lazily
    import httpx

__all__ = ["UnsiloedDenialParserAdapter"]

_DEFAULT_BASE_URL = "https://prod.visionapi.unsiloed.ai"
_DEFAULT_CONFIDENCE_FLOOR = 0.85

# Async job model: create is POST /v2/extract, poll is GET /extract/{job_id}.
_CREATE_PATH = "/v2/extract"
_POLL_PATH = "/extract/{job_id}"

# Terminal status strings (case-insensitive comparison handles 'Succeeded' vs
# 'succeeded' — the docs leave the casing uncertain).
_SUCCESS_STATES = frozenset({"succeeded", "success", "completed", "complete"})
_FAILURE_STATES = frozenset({"failed", "error", "cancelled", "canceled"})

# Capped exponential backoff for the poll loop.
_POLL_BASE_DELAY_S = 0.5
_POLL_MAX_DELAY_S = 5.0
_POLL_MAX_ATTEMPTS = 12

# Artifact kinds Unsiloed vision parsing serves (image/PDF paper forms).
_SUPPORTED_KINDS = frozenset(
    {
        ArtifactKind.EOB,
        ArtifactKind.CMS1500,
        ArtifactKind.UB04,
        ArtifactKind.PDF_IMAGE,
    }
)
# Raw EDI is handled by the deterministic parser, never sent to the network.
_EDI_KINDS = frozenset(
    {ArtifactKind.X12_835, ArtifactKind.X12_837, ArtifactKind.X12_277}
)

_MIME_BY_KIND: Dict[ArtifactKind, str] = {
    ArtifactKind.EOB: "application/pdf",
    ArtifactKind.CMS1500: "application/pdf",
    ArtifactKind.UB04: "application/pdf",
    ArtifactKind.PDF_IMAGE: "application/pdf",
}


class UnsiloedDenialParserAdapter:
    """Real :class:`DenialParserPort` backed by the Unsiloed vision API.

    Args:
        client: An injected :class:`httpx.AsyncClient` (the adapter never builds
            its own transport — that is owned by ``infra.http_client``).
        api_key: The Unsiloed API key, sent in the ``api-key`` header.
        base_url: API host; defaults to ``https://prod.visionapi.unsiloed.ai``.
        confidence_floor: Overall-confidence threshold below which
            ``needs_human_review`` is ``True`` (defaults to ``0.85``).
        poll_max_attempts: Bounded poll budget before :class:`UnsiloedTimeout`.
    """

    def __init__(
        self,
        client: httpx.AsyncClient,
        api_key: str,
        base_url: str = _DEFAULT_BASE_URL,
        confidence_floor: float = _DEFAULT_CONFIDENCE_FLOOR,
        poll_max_attempts: int = _POLL_MAX_ATTEMPTS,
    ) -> None:
        """Bind the injected client, credential, host and review floor."""
        self._client = client
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._floor = confidence_floor
        self._poll_max_attempts = poll_max_attempts

    # ----------------------------------------------------------------- #
    # Port surface.
    # ----------------------------------------------------------------- #
    def supports(self, kind: ArtifactKind) -> bool:
        """Return ``True`` for image/PDF kinds, ``False`` for raw EDI."""
        return kind in _SUPPORTED_KINDS

    async def parse(self, req: ParseRequest) -> DenialExtraction:
        """Extract Denial fields from the artifact bytes via Unsiloed.

        Raises:
            UnsupportedArtifact: For raw EDI kinds (delegates to the sim).
            UnsiloedAuthError: On a 401/403 from the vendor.
            UnsiloedJobFailed: On a terminal failure status.
            UnsiloedTimeout: When the poll budget is exhausted.
        """
        if req.kind in _EDI_KINDS or req.kind not in _SUPPORTED_KINDS:
            raise UnsupportedArtifact(req.kind)

        job_id = await self._create_job(req)
        body = await self._poll_job(job_id)
        return self._map_result(req.kind, body)

    # ----------------------------------------------------------------- #
    # HTTP flow (httpx imported lazily inside).
    # ----------------------------------------------------------------- #
    async def _create_job(self, req: ParseRequest) -> str:
        """POST the artifact bytes + schema and return the vendor job id."""
        import httpx  # lazy: keeps the module importable without the SDK

        filename = req.filename or "denial.pdf"
        mime = _MIME_BY_KIND.get(req.kind, "application/octet-stream")
        files = {"file": (filename, req.content, mime)}
        data = {"schema_data": json.dumps(fields.build_denial_schema())}

        try:
            resp = await self._client.post(
                f"{self._base_url}{_CREATE_PATH}",
                headers=self._headers(),
                files=files,
                data=data,
            )
        except httpx.HTTPError as exc:  # network/transport fault
            raise UnsiloedTimeout() from exc

        self._raise_for_auth(resp)
        if resp.status_code not in (200, 201):
            raise UnsiloedJobFailed(status=str(resp.status_code))

        payload = self._json(resp)
        job_id = payload.get("job_id")
        if not isinstance(job_id, str) or not job_id:
            raise UnsiloedJobFailed(status="missing job_id")
        return job_id

    async def _poll_job(self, job_id: str) -> Dict[str, Any]:
        """Poll the job until a terminal status, then return its flat body."""
        import httpx  # lazy

        url = f"{self._base_url}{_POLL_PATH.format(job_id=job_id)}"
        delay = _POLL_BASE_DELAY_S
        for _ in range(self._poll_max_attempts):
            try:
                resp = await self._client.get(url, headers=self._headers())
            except httpx.HTTPError as exc:
                raise UnsiloedTimeout(job_id=job_id) from exc

            self._raise_for_auth(resp)
            if resp.status_code not in (200, 201):
                raise UnsiloedJobFailed(job_id=job_id, status=str(resp.status_code))

            body = self._json(resp)
            status = str(body.get("status", "")).strip().lower()
            if status in _SUCCESS_STATES or (not status and _looks_terminal(body)):
                return body
            if status in _FAILURE_STATES:
                raise UnsiloedJobFailed(job_id=job_id, status=status or "failed")

            await asyncio.sleep(delay)
            delay = min(delay * 2, _POLL_MAX_DELAY_S)

        raise UnsiloedTimeout(job_id=job_id)

    # ----------------------------------------------------------------- #
    # Response mapping (pure).
    # ----------------------------------------------------------------- #
    def _map_result(self, kind: ArtifactKind, body: Dict[str, Any]) -> DenialExtraction:
        """Map the flat Unsiloed field-map into a :class:`DenialExtraction`."""
        extracted: List[ExtractedField] = []
        for name in fields.CANONICAL_FIELDS:
            entry = body.get(name)
            if not isinstance(entry, dict):
                continue
            field = self._map_field(name, entry)
            if field is not None:
                extracted.append(field)

        overall = self._overall_confidence(body, extracted)
        return DenialExtraction(
            kind=kind,
            fields=tuple(extracted),
            overall_confidence=overall,
            needs_human_review=overall < self._floor,
        )

    @staticmethod
    def _map_field(name: str, entry: Dict[str, Any]) -> Optional[ExtractedField]:
        """Convert one ``{value, score, bboxes, page_no}`` entry to a field."""
        value = entry.get("value")
        if value is None:
            return None
        if isinstance(value, (list, tuple)):
            rendered = ",".join(str(v) for v in value)
        else:
            rendered = str(value)
        score = _clamp01(_as_float(entry.get("score"), default=0.0))
        page_no = entry.get("page_no")
        provenance: Dict[str, str] = {"engine": "unsiloed"}
        bbox = _first_bbox(entry.get("bboxes"))
        if bbox is not None:
            provenance["bbox"] = bbox
        source_text = _first_bbox_text(entry.get("bboxes"))
        if source_text:
            provenance["source_text"] = source_text
        return ExtractedField(
            name=name,
            value=rendered,
            confidence=score,
            page_no=int(page_no) if isinstance(page_no, (int, float)) else None,
            provenance=provenance,
        )

    @staticmethod
    def _overall_confidence(
        body: Dict[str, Any], extracted: List[ExtractedField]
    ) -> float:
        """Resolve overall confidence from ``min_confidence_score`` or the mean."""
        raw = body.get("min_confidence_score")
        if isinstance(raw, (int, float)):
            return _clamp01(float(raw))
        scored = [f.confidence for f in extracted]
        if not scored:
            return 0.0
        return _clamp01(sum(scored) / len(scored))

    # ----------------------------------------------------------------- #
    # Small helpers.
    # ----------------------------------------------------------------- #
    def _headers(self) -> Dict[str, str]:
        """Return request headers; the credential travels in ``api-key``."""
        return {"api-key": self._api_key, "accept": "application/json"}

    def _raise_for_auth(self, resp: httpx.Response) -> None:
        """Translate a 401/403 into :class:`UnsiloedAuthError`."""
        if resp.status_code in (401, 403):
            raise UnsiloedAuthError(status_code=resp.status_code)

    @staticmethod
    def _json(resp: httpx.Response) -> Dict[str, Any]:
        """Decode a JSON object body, normalising a non-object to ``{}``."""
        try:
            data = resp.json()
        except ValueError as exc:
            raise UnsiloedJobFailed(status="invalid json") from exc
        if not isinstance(data, dict):
            raise UnsiloedJobFailed(status="unexpected body")
        return data


def _looks_terminal(body: Dict[str, Any]) -> bool:
    """Return ``True`` if a status-less body already carries extracted fields.

    Some responses omit the ``status`` key once a job is done and simply return
    the field-map; treat the presence of any canonical field (or the overall
    score) as a terminal signal.
    """
    if "min_confidence_score" in body:
        return True
    return any(name in body for name in fields.CANONICAL_FIELDS)


def _as_float(value: Any, default: float) -> float:
    """Best-effort float coercion with a fallback."""
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value))
    except (TypeError, ValueError):
        return default


def _clamp01(value: float) -> float:
    """Clamp a confidence into ``[0, 1]``."""
    return max(0.0, min(1.0, value))


def _first_bbox(bboxes: Any) -> Optional[str]:
    """Render the first bounding box as ``"l,t,r,b"`` provenance, if present."""
    if not isinstance(bboxes, list) or not bboxes:
        return None
    first = bboxes[0]
    if not isinstance(first, dict):
        return None
    box = first.get("bbox")
    if isinstance(box, (list, tuple)):
        return ",".join(str(v) for v in box)
    if isinstance(box, str):
        return box
    return None


def _first_bbox_text(bboxes: Any) -> Optional[str]:
    """Return the OCR text of the first bounding box, if present."""
    if not isinstance(bboxes, list) or not bboxes:
        return None
    first = bboxes[0]
    if isinstance(first, dict):
        text = first.get("text")
        if isinstance(text, str) and text:
            return text
    return None


# Tuple of the supported real-mode kinds, exposed for composition wiring/tests.
SUPPORTED_KINDS: Tuple[ArtifactKind, ...] = tuple(sorted(_SUPPORTED_KINDS, key=lambda k: k.value))

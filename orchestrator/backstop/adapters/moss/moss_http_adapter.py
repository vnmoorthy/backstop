"""REAL ``RetrievalPort`` adapter — Moss project-scoped semantic retrieval.

One responsibility: translate a :class:`RetrievalQuery` into
``POST {MOSS_BASE_URL}/v1/query`` over Moss's project-scoped REST API and map the
JSON response back into the port's DTOs. The adapter does NOT construct its own
transport or read the environment — a shared :class:`httpx.AsyncClient` and the
project credentials are injected at the composition root (single secret-load
site, fully testable via :class:`httpx.MockTransport`).

``httpx`` is the vendor dependency here; it is imported LAZILY inside the methods
that touch the wire so this module imports cleanly even when ``httpx`` is absent.
All vendor/transport faults are caught and re-raised as the domain
:class:`backstop.domain.errors.RetrievalError`, so nothing ``httpx``-shaped ever
escapes the port. On a 5xx or transport error the adapter performs exactly one
bounded retry before giving up.

PHI handling: the query is built upstream from de-identified denial context
(CARC/RARC, payer, CPT, place-of-service). As defense-in-depth this adapter
asserts the outbound text matches no member-id/SSN pattern and raises a
``RetrievalError`` rather than letting PHI cross the boundary.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Dict, List, Mapping, Optional

from backstop.domain.enums import IntegrationMode
from backstop.domain.errors import RetrievalError
from backstop.ports.retrieval_port import (
    EvidenceChunk,
    RetrievalHealth,
    RetrievalQuery,
    RetrievalResult,
)

if TYPE_CHECKING:  # pragma: no cover - typing only, no runtime vendor import.
    import httpx

__all__ = ["MossEndpoints", "MossHttpAdapter"]

# Defense-in-depth PHI guards on the OUTBOUND query string. These are coarse
# surrogates: a 9-digit run (SSN), an explicit member/subscriber-id phrase, or a
# bare SSN with dashes. The Service redacts upstream; this is the last gate.
_SSN_RE = re.compile(r"\b\d{3}-?\d{2}-?\d{4}\b")
_MEMBER_ID_RE = re.compile(
    r"\b(?:member|subscriber|mrn|patient)\s*(?:id|#|no)?\s*[:#]?\s*\w*\d",
    re.IGNORECASE,
)

# Bounded retry budget: the initial attempt plus exactly one retry on 5xx/timeout.
_MAX_ATTEMPTS = 2


@dataclass(frozen=True)
class MossEndpoints:
    """Isolated Moss REST path/header config (a one-file fix if the API differs).

    Attributes:
        query_path: Path of the load-bearing semantic-query endpoint.
        health_path: Path of the liveness probe.
        auth_header: Header carrying the bearer project key.
        project_header: Header carrying the project id.
        rerank: Whether to request server-side reranking of candidates.
    """

    query_path: str = "/v1/query"
    health_path: str = "/v1/health"
    auth_header: str = "Authorization"
    project_header: str = "X-Moss-Project"
    rerank: bool = True


class MossHttpAdapter:
    """Project-scoped Moss retrieval over an injected async HTTP client."""

    def __init__(
        self,
        client: httpx.AsyncClient,
        project_id: str,
        project_key: str,
        endpoints: Optional[MossEndpoints] = None,
    ) -> None:
        """Wire the adapter to a shared client and project credentials.

        Args:
            client: A shared :class:`httpx.AsyncClient` (injected, not created
                here). Its ``base_url`` is expected to be ``MOSS_BASE_URL``.
            project_id: The Moss project id (``MOSS_PROJECT_ID``), sent as a
                header on every request.
            project_key: The secret project key (``MOSS_PROJECT_KEY``), sent as a
                bearer token. Never logged.
            endpoints: Optional path/header overrides; sensible Moss defaults are
                used when omitted.
        """
        self._client = client
        self._project_id = project_id
        self._project_key = project_key
        self._endpoints = endpoints or MossEndpoints()

    # ------------------------------------------------------------------ #
    # Port surface.
    # ------------------------------------------------------------------ #
    async def retrieve(self, query: RetrievalQuery) -> RetrievalResult:
        """Retrieve ranked evidence for ``query`` within ``query.timeout_s``.

        Args:
            query: The PHI-free retrieval request.

        Returns:
            A :class:`RetrievalResult` with chunks ordered by descending score,
            ``len <= top_k``, scores normalized to ``[0, 1]``. A no-match
            response yields an empty result, not an error.

        Raises:
            RetrievalError: On a PHI guard trip, after a bounded retry on
                5xx/timeout, on any transport/decoding fault, or on a non-2xx
                client error. Never raises a vendor (``httpx``) exception.
        """
        self._assert_no_phi(query.text)
        payload = self._build_payload(query)
        data = await self._post_with_retry(payload, query.timeout_s)
        return self._map_result(data, query.top_k)

    async def health(self) -> RetrievalHealth:
        """Probe ``/v1/health``; never raises.

        Returns:
            ``ok=True`` iff the probe returns a 2xx; the active mode is always
            ``real``. Any fault is folded into ``ok=False`` with a safe detail.
        """
        try:
            import httpx  # Lazy: keep module import clean without the SDK.

            response = await self._client.get(
                self._endpoints.health_path,
                headers=self._auth_headers(),
            )
            ok = response.is_success
            detail = f"moss health: HTTP {response.status_code}"
        except httpx.HTTPError as exc:  # pragma: no cover - exercised via mock
            ok = False
            detail = f"moss unreachable: {type(exc).__name__}"
        return RetrievalHealth(ok=ok, mode=IntegrationMode.REAL, detail=detail)

    # ------------------------------------------------------------------ #
    # Wire helpers.
    # ------------------------------------------------------------------ #
    def _auth_headers(self) -> Dict[str, str]:
        """Build project-scoped auth headers (bearer key + project id)."""
        return {
            self._endpoints.auth_header: f"Bearer {self._project_key}",
            self._endpoints.project_header: self._project_id,
        }

    def _build_payload(self, query: RetrievalQuery) -> Dict[str, Any]:
        """Translate the query DTO into the Moss request body."""
        filters: Dict[str, str] = {}
        if query.payer_id is not None:
            filters["payer"] = query.payer_id
        if query.carc is not None:
            filters["carc"] = query.carc
        payload: Dict[str, Any] = {
            "query": query.text,
            "top_k": query.top_k,
            "rerank": self._endpoints.rerank,
        }
        if filters:
            payload["filters"] = filters
        return payload

    async def _post_with_retry(
        self, payload: Mapping[str, Any], timeout_s: float
    ) -> Mapping[str, Any]:
        """POST the query with exactly one bounded retry on 5xx/timeout.

        Translates every vendor fault into :class:`RetrievalError`.
        """
        import httpx  # Lazy import — module stays importable without the SDK.

        last_exc: Optional[BaseException] = None
        for _attempt in range(_MAX_ATTEMPTS):
            try:
                response = await self._client.post(
                    self._endpoints.query_path,
                    json=dict(payload),
                    headers=self._auth_headers(),
                    timeout=timeout_s,
                )
            except httpx.TimeoutException as exc:
                last_exc = exc
                continue  # Retry once on timeout.
            except httpx.HTTPError as exc:
                # Non-timeout transport faults are not retried.
                raise RetrievalError("moss retrieval transport error") from exc

            status = response.status_code
            if status >= 500:
                last_exc = RetrievalError(f"moss retrieval server error: HTTP {status}")
                continue  # Retry once on 5xx.
            if status >= 400:
                # Client errors (auth, bad request) are terminal — do not retry.
                raise RetrievalError(f"moss retrieval client error: HTTP {status}")

            try:
                body = response.json()
            except (ValueError, httpx.HTTPError) as exc:
                raise RetrievalError("moss retrieval returned undecodable body") from exc
            if not isinstance(body, Mapping):
                raise RetrievalError("moss retrieval returned a non-object body")
            return body

        raise RetrievalError("moss retrieval failed after bounded retry") from last_exc

    def _map_result(self, data: Mapping[str, Any], top_k: int) -> RetrievalResult:
        """Map a Moss response body into a :class:`RetrievalResult`."""
        raw_results = data.get("results", [])
        if not isinstance(raw_results, list):
            raise RetrievalError("moss retrieval response missing a results array")

        chunks: List[EvidenceChunk] = []
        for raw in raw_results:
            chunk = self._map_chunk(raw)
            if chunk is not None:
                chunks.append(chunk)

        # Defensively enforce descending score and the top_k bound; the server
        # already ranks, but the port contract is ours to guarantee.
        chunks.sort(key=lambda c: c.score, reverse=True)
        bounded = tuple(chunks[: max(top_k, 0)])

        query_id = data.get("query_id")
        return RetrievalResult(
            chunks=bounded,
            query_id=query_id if isinstance(query_id, str) else None,
        )

    def _map_chunk(self, raw: Any) -> Optional[EvidenceChunk]:
        """Map one raw result object into an :class:`EvidenceChunk`, or skip it."""
        if not isinstance(raw, Mapping):
            return None
        chunk_id = raw.get("id")
        text = raw.get("text")
        if not isinstance(chunk_id, str) or not isinstance(text, str):
            return None

        score = _coerce_unit_score(raw.get("score"))
        metadata_obj = raw.get("metadata")
        metadata = _coerce_metadata(metadata_obj)
        source = ""
        if isinstance(metadata_obj, Mapping):
            raw_source = metadata_obj.get("source")
            if isinstance(raw_source, str):
                source = raw_source

        return EvidenceChunk(
            chunk_id=chunk_id,
            text=text,
            score=score,
            source=source,
            metadata=metadata,
        )

    # ------------------------------------------------------------------ #
    # PHI defense-in-depth.
    # ------------------------------------------------------------------ #
    @staticmethod
    def _assert_no_phi(text: str) -> None:
        """Raise if the outbound query text looks like it carries PHI.

        Args:
            text: The query string about to leave the boundary.

        Raises:
            RetrievalError: If an SSN or member-id pattern is detected.
        """
        if _SSN_RE.search(text) or _MEMBER_ID_RE.search(text):
            raise RetrievalError("refusing to send a query that may contain PHI")


def _coerce_unit_score(value: Any) -> float:
    """Coerce a raw score into ``[0, 1]``; unparseable values map to ``0.0``."""
    try:
        score = float(value)
    except (TypeError, ValueError):
        return 0.0
    if score != score:  # NaN
        return 0.0
    if score < 0.0:
        return 0.0
    if score > 1.0:
        return 1.0
    return score


def _coerce_metadata(value: Any) -> Dict[str, str]:
    """Coerce a raw metadata object into a ``Dict[str, str]`` (non-PHI fields)."""
    if not isinstance(value, Mapping):
        return {}
    out: Dict[str, str] = {}
    for key, val in value.items():
        if isinstance(key, str) and val is not None:
            out[key] = val if isinstance(val, str) else str(val)
    return out

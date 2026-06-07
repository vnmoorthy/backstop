"""TrueFoundryGatewayAdapter — the real LLM chokepoint over the TFY gateway.

Implements :class:`~backstop.ports.llm_gateway_port.LLMGatewayPort`. It is injected
the SAME ``RedactionPort`` / ``AuditLogPort`` / ``CostLedgerPort`` singletons as the
sim adapter, so redaction + tamper-evident audit + priced cost are shared local
work; only the model proxy differs.

complete() flow: redact-out (re-asserted at the boundary) -> POST the redacted
OpenAI-shape body to ``{base_url}{inference_path}/chat/completions`` with a Bearer
key and ``X-TFY-METADATA`` -> bounded retry on 429/5xx honouring ``Retry-After`` ->
redact-in -> audit -> cost -> PHI-free response. On any non-2xx after retry it
raises :class:`~backstop.adapters.truefoundry.GatewayError`; it never silently
degrades and never writes a success audit/cost row for a failed call.

The ``httpx`` SDK is imported LAZILY inside the request methods so the module
imports cleanly when httpx is absent (and so the contract test can mock it).
Vendor/runtime errors are translated to domain errors at the boundary.
"""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING, Any, AsyncIterator, Dict, FrozenSet, List, Optional, Tuple

from backstop.adapters.truefoundry import GatewayError
from backstop.adapters.truefoundry.gateway_common import finalize_call
from backstop.domain.enums import IntegrationMode
from backstop.ports.audit_log_port import AuditLogPort
from backstop.ports.cost_ledger_port import CostLedgerPort
from backstop.ports.llm_gateway_port import (
    GatewayCostSnapshot,
    GatewayHealth,
    LLMChunk,
    LLMRequest,
    LLMResponse,
)
from backstop.ports.redaction_port import RedactionPort

if TYPE_CHECKING:  # pragma: no cover - typing only

    from backstop.infra.config import Settings

__all__ = ["TrueFoundryGatewayAdapter"]

# Status codes that warrant a bounded retry (transient); everything else
# (auth/validation) fails immediately without retry.
_RETRYABLE: FrozenSet[int] = frozenset({429, 500, 502, 503, 504})
_MAX_ATTEMPTS: int = 3
_BACKOFF_BASE_S: float = 0.2


class TrueFoundryGatewayAdapter:
    """Real LLM chokepoint over TrueFoundry's OpenAI-compatible gateway."""

    def __init__(
        self,
        *,
        redaction: RedactionPort,
        audit: AuditLogPort,
        cost: CostLedgerPort,
        api_key: Optional[str],
        base_url: str,
        inference_path: str,
        default_model: str,
        client: Optional[Any] = None,
    ) -> None:
        """Inject the shared singletons and gateway connection settings.

        Args:
            redaction: Shared PHI redactor (same instance as the sim adapter).
            audit: Shared hash-chained audit sink.
            cost: Shared priced cost ledger.
            api_key: TrueFoundry bearer key, or ``None`` (auth header still sent).
            base_url: Gateway host base URL.
            inference_path: OpenAI-compatible inference path prefix.
            default_model: Model slug used when a request omits an override.
            client: Optional injected ``httpx.AsyncClient`` (used by tests with a
                ``MockTransport``); when ``None`` the adapter builds its own
                lazily-imported client per request.
        """
        self._redaction = redaction
        self._audit = audit
        self._cost = cost
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._inference_path = "/" + inference_path.strip("/")
        self._default_model = default_model
        self._client = client

    @classmethod
    def from_settings(
        cls,
        settings: Settings,
        *,
        redaction: RedactionPort,
        audit: AuditLogPort,
        cost: CostLedgerPort,
        client: Optional[Any] = None,
    ) -> TrueFoundryGatewayAdapter:
        """Build from frozen :class:`Settings`."""
        return cls(
            redaction=redaction,
            audit=audit,
            cost=cost,
            api_key=settings.truefoundry_api_key,
            base_url=settings.truefoundry_base_url,
            inference_path=settings.truefoundry_inference_path,
            default_model=settings.truefoundry_default_model,
            client=client,
        )

    @property
    def mode(self) -> IntegrationMode:
        """This adapter always reports ``real``."""
        return IntegrationMode.REAL

    @property
    def _chat_url(self) -> str:
        """Absolute chat-completions endpoint URL."""
        return f"{self._base_url}{self._inference_path}/chat/completions"

    @property
    def _models_url(self) -> str:
        """Absolute models-list endpoint URL (health preflight)."""
        return f"{self._base_url}{self._inference_path}/models"

    def _headers(self, req: LLMRequest) -> Dict[str, str]:
        """Build the request headers, including the per-call trace metadata."""
        return {
            "Authorization": f"Bearer {self._api_key or ''}",
            "Content-Type": "application/json",
            "X-TFY-METADATA": json.dumps(
                {"appeal_id": req.appeal_id, "stage": req.stage}
            ),
        }

    # ----------------------------------------------------------------- #
    # LLMGatewayPort.
    # ----------------------------------------------------------------- #
    async def complete(self, req: LLMRequest) -> LLMResponse:
        """Run a redacted completion upstream, then audit + price it."""
        model = req.model or self._default_model
        safe_prompt, body = self._build_request(req, model, stream=False)
        payload = await self._post_with_retry(self._chat_url, self._headers(req), body)
        text, finish_reason, request_id = self._parse_completion(payload)
        return finalize_call(
            redaction=self._redaction,
            audit=self._audit,
            cost=self._cost,
            mode=IntegrationMode.REAL,
            appeal_id=req.appeal_id,
            stage=req.stage,
            model=model,
            redacted_prompt_text=safe_prompt,
            raw_completion=text,
            finish_reason=finish_reason,
            gateway_request_id=request_id,
        )

    async def stream(self, req: LLMRequest) -> AsyncIterator[LLMChunk]:
        """Stream a redacted completion, buffering to sentence boundaries.

        The upstream SSE deltas are buffered and only flushed (redacted) at
        sentence boundaries so PHI never streams mid-token. The full completion is
        accounted (audit + cost) once at the end via the shared tail.
        """
        model = req.model or self._default_model
        safe_prompt, body = self._build_request(req, model, stream=True)
        buffer = ""
        full: List[str] = []
        finish_reason: Optional[str] = None
        request_id: Optional[str] = None
        async for event in self._stream_events(self._chat_url, self._headers(req), body):
            delta, reason, rid = event
            if rid is not None:
                request_id = rid
            if reason is not None:
                finish_reason = reason
            if delta:
                buffer += delta
                full.append(delta)
                flush, buffer = _take_sentences(buffer)
                if flush:
                    yield LLMChunk(delta=self._redaction.redact_text(flush), finish_reason=None)
        if buffer:
            yield LLMChunk(delta=self._redaction.redact_text(buffer), finish_reason=None)
        finalize_call(
            redaction=self._redaction,
            audit=self._audit,
            cost=self._cost,
            mode=IntegrationMode.REAL,
            appeal_id=req.appeal_id,
            stage=req.stage,
            model=model,
            redacted_prompt_text=safe_prompt,
            raw_completion="".join(full),
            finish_reason=finish_reason or "stop",
            gateway_request_id=request_id,
        )
        yield LLMChunk(
            delta=self._redaction.redact_text(""), finish_reason=finish_reason or "stop"
        )

    def health(self) -> GatewayHealth:
        """Preflight the gateway; never raises (degrades to unreachable)."""
        try:
            authorized = self._models_list_contains(self._default_model)
            return GatewayHealth(
                ok=True,
                mode=IntegrationMode.REAL,
                default_model=self._default_model,
                detail=("model authorized" if authorized else "model not authorized"),
            )
        except Exception as exc:  # - health must never raise
            return GatewayHealth(
                ok=False,
                mode=IntegrationMode.REAL,
                default_model=self._default_model,
                detail=f"unreachable: {type(exc).__name__}",
            )

    def cost_to_date(self, appeal_id: Optional[str] = None) -> GatewayCostSnapshot:
        """Read aggregated spend through the shared cost ledger."""
        snap = self._cost.snapshot(appeal_id)
        return GatewayCostSnapshot(
            total=snap.total, appeal_id=snap.appeal_id, by_stage=dict(snap.by_stage)
        )

    # ----------------------------------------------------------------- #
    # Request building + redact-out.
    # ----------------------------------------------------------------- #
    def _build_request(
        self, req: LLMRequest, model: str, *, stream: bool
    ) -> Tuple[str, Dict[str, Any]]:
        """Build the redacted OpenAI-shape body and the flattened prompt text.

        Re-asserts outbound redaction on every message content (defence-in-depth)
        even though the port type already requires ``RedactedText``.
        """
        messages: List[Dict[str, str]] = []
        flat: List[str] = []
        for m in req.messages:
            safe = self._redaction.redact_text(m.content.text).text
            messages.append({"role": m.role, "content": safe})
            flat.append(f"{m.role}: {safe}")
        body: Dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": req.temperature,
            "stream": stream,
        }
        if req.max_tokens is not None:
            body["max_tokens"] = req.max_tokens
        return "\n".join(flat), body

    @staticmethod
    def _parse_completion(payload: Dict[str, Any]) -> Tuple[str, Optional[str], Optional[str]]:
        """Extract ``(text, finish_reason, request_id)`` from an OpenAI response."""
        request_id = payload.get("id")
        choices = payload.get("choices") or []
        if not choices:
            raise GatewayError("gateway returned no choices")
        choice = choices[0]
        message = choice.get("message") or {}
        text = message.get("content") or ""
        finish_reason = choice.get("finish_reason")
        return (
            str(text),
            None if finish_reason is None else str(finish_reason),
            None if request_id is None else str(request_id),
        )

    # ----------------------------------------------------------------- #
    # HTTP (lazy httpx; bounded retry; error translation).
    # ----------------------------------------------------------------- #
    def _http(self) -> Any:
        """Return the injected client or a lazily-built ``httpx.AsyncClient``.

        The ``httpx`` import lives here so the module imports without httpx
        installed; tests inject a client over a ``MockTransport`` instead.
        """
        if self._client is not None:
            return self._client
        import httpx  # - lazy: real adapter is the only I/O site

        timeout = httpx.Timeout(connect=5.0, read=30.0, write=30.0, pool=5.0)
        return httpx.AsyncClient(timeout=timeout)

    async def _post_with_retry(
        self, url: str, headers: Dict[str, str], body: Dict[str, Any]
    ) -> Dict[str, Any]:
        """POST with bounded retry on transient status; translate errors.

        Retries 429/5xx up to :data:`_MAX_ATTEMPTS`, honouring ``Retry-After``;
        4xx (e.g. auth) raise immediately; persistent failure raises
        :class:`GatewayError`. On success returns the parsed JSON body.
        """
        client = self._http()
        owns = self._client is None
        try:
            last_status = 0
            for attempt in range(1, _MAX_ATTEMPTS + 1):
                response = await client.post(url, headers=headers, json=body)
                status = response.status_code
                if 200 <= status < 300:
                    return self._json(response)
                last_status = status
                if status not in _RETRYABLE or attempt == _MAX_ATTEMPTS:
                    raise GatewayError(
                        f"gateway returned HTTP {status}", status_code=status
                    )
                await asyncio.sleep(self._retry_delay(response, attempt))
            raise GatewayError(  # pragma: no cover - loop always returns/raises
                f"gateway failed after {_MAX_ATTEMPTS} attempts", status_code=last_status
            )
        except GatewayError:
            raise
        except Exception as exc:  # - translate any transport fault
            raise GatewayError(f"gateway transport error: {type(exc).__name__}") from exc
        finally:
            if owns:
                await client.aclose()

    async def _stream_events(
        self, url: str, headers: Dict[str, str], body: Dict[str, Any]
    ) -> AsyncIterator[Tuple[str, Optional[str], Optional[str]]]:
        """Yield ``(delta, finish_reason, request_id)`` from an SSE stream.

        Parses ``data:`` lines of the OpenAI streaming schema; stops on
        ``[DONE]``. Translates transport faults to :class:`GatewayError`.
        """
        client = self._http()
        owns = self._client is None
        try:
            async with client.stream("POST", url, headers=headers, json=body) as response:
                status = response.status_code
                if not (200 <= status < 300):
                    raise GatewayError(
                        f"gateway returned HTTP {status}", status_code=status
                    )
                async for line in response.aiter_lines():
                    parsed = _parse_sse_line(line)
                    if parsed is _DONE:
                        return
                    if parsed is not None:
                        yield parsed
        except GatewayError:
            raise
        except Exception as exc:  # - translate any transport fault
            raise GatewayError(f"gateway stream error: {type(exc).__name__}") from exc
        finally:
            if owns:
                await client.aclose()

    def _models_list_contains(self, model: str) -> bool:
        """Return whether ``models_list`` reports *model* as authorized.

        Synchronous preflight used by :meth:`health`; builds a short-lived sync
        client when no async client is injected so health stays non-async.
        """
        import httpx  # - lazy: real adapter is the only I/O site

        headers = {"Authorization": f"Bearer {self._api_key or ''}"}
        timeout = httpx.Timeout(connect=5.0, read=10.0, write=10.0, pool=5.0)
        with httpx.Client(timeout=timeout) as client:
            response = client.get(self._models_url, headers=headers)
            response.raise_for_status()
            data = response.json().get("data") or []
        return any(entry.get("id") == model for entry in data)

    @staticmethod
    def _retry_delay(response: Any, attempt: int) -> float:
        """Compute the backoff delay, honouring ``Retry-After`` when present."""
        retry_after = None
        try:
            retry_after = response.headers.get("Retry-After")
        except Exception:  # - headers may be absent on mock responses
            retry_after = None
        if retry_after is not None:
            try:
                return float(retry_after)
            except ValueError:
                pass
        return _BACKOFF_BASE_S * float(2 ** (attempt - 1))

    @staticmethod
    def _json(response: Any) -> Dict[str, Any]:
        """Parse a JSON response body into a dict, translating parse errors."""
        try:
            data = response.json()
        except Exception as exc:  # - bad body is a gateway fault
            raise GatewayError("gateway returned non-JSON body") from exc
        if not isinstance(data, dict):
            raise GatewayError("gateway returned a non-object JSON body")
        return data


# Sentinel marking the SSE terminator line.
_DONE: object = object()


def _parse_sse_line(line: str) -> Optional[Any]:
    """Parse one SSE ``data:`` line into a delta tuple or the DONE sentinel.

    Returns ``None`` for non-data / keep-alive lines, ``_DONE`` for the
    terminator, else ``(delta, finish_reason, request_id)``.
    """
    if not line or not line.startswith("data:"):
        return None
    data = line[len("data:") :].strip()
    if data == "[DONE]":
        return _DONE
    try:
        payload = json.loads(data)
    except json.JSONDecodeError:
        return None
    request_id = payload.get("id")
    choices = payload.get("choices") or []
    if not choices:
        return ("", None, None if request_id is None else str(request_id))
    choice = choices[0]
    delta = (choice.get("delta") or {}).get("content") or ""
    finish_reason = choice.get("finish_reason")
    return (
        str(delta),
        None if finish_reason is None else str(finish_reason),
        None if request_id is None else str(request_id),
    )


# Sentence-boundary punctuation used by the streaming buffer.
_SENTENCE_ENDERS: FrozenSet[str] = frozenset({".", "!", "?", "\n"})


def _take_sentences(buffer: str) -> Tuple[str, str]:
    """Split *buffer* into ``(complete_sentences, remainder)``.

    Flushes everything up to and including the last sentence-ending character so
    a chunk is never flushed mid-PHI-token; the remainder stays buffered.
    """
    last = -1
    for i, ch in enumerate(buffer):
        if ch in _SENTENCE_ENDERS:
            last = i
    if last == -1:
        return "", buffer
    return buffer[: last + 1], buffer[last + 1 :]

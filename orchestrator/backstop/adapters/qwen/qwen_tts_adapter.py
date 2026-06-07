"""Real :class:`SpeechSynthesisPort` — Qwen / DashScope brand-voice TTS.

``QwenTtsAdapter`` translates a :class:`SynthRequest` to the Alibaba Cloud Model
Studio (DashScope) multimodal-generation REST call, fetches the resulting audio
(either a short-lived ``output.audio.url`` or an inline ``preview_audio.data``
base64 payload), validates it parses as a real mono/16-bit PCM WAV, and returns
a :class:`SynthResult`. The streaming path opens the DashScope realtime WebSocket
and reassembles ``response.audio.delta`` frames into cancellable
:class:`AudioFrame` s for nurse barge-in.

Vendor I/O lives ONLY here. The vendor client (``httpx``) is imported *lazily*
inside the methods that use it, so this module imports cleanly even when the SDK
/ httpx extras are absent — a missing vendor must never block the contract gate
(the test injects a mocked transport). Every vendor/runtime fault is normalized
to :class:`SynthError`; raw httpx exceptions never cross the port. Per the
no-BAA constraint, ``req.text`` must already be redacted upstream and is never
logged.
"""

from __future__ import annotations

import base64
from typing import TYPE_CHECKING, Any, AsyncIterator, Dict, Optional

from backstop.adapters.qwen.errors import SynthError, SynthErrorKind
from backstop.adapters.qwen.wav_codec import (
    SAMPLE_WIDTH_BYTES,
    is_valid_pcm_wav,
    pcm_to_wav,
    wav_to_pcm,
)
from backstop.domain.enums import IntegrationMode
from backstop.ports.speech_synthesis_port import (
    AudioFrame,
    SynthHealth,
    SynthRequest,
    SynthResult,
)

if TYPE_CHECKING:  # pragma: no cover - typing only (no runtime vendor import)
    import httpx

__all__ = ["QwenTtsAdapter"]

# DashScope endpoints (region-selected). The non-streaming generation endpoint
# returns ``output.audio.url`` (24h expiry) or inline base64 preview data.
_GENERATION_PATH = "/api/v1/services/aigc/multimodal-generation/generation"

_REGION_BASE_URLS: Dict[str, str] = {
    "intl": "https://dashscope-intl.aliyuncs.com",
    "cn": "https://dashscope.aliyuncs.com",
}

# BCP-47-ish language tag -> DashScope ``language_type``.
_LANGUAGE_TYPE: Dict[str, str] = {
    "en": "English",
    "en-us": "English",
    "zh": "Chinese",
    "zh-cn": "Chinese",
    "es": "Spanish",
    "es-es": "Spanish",
}

_DEFAULT_TIMEOUT_S: float = 30.0


class QwenTtsAdapter:
    """DashScope TTS adapter honouring :class:`SpeechSynthesisPort`.

    Collaborators are injected (no module-level globals, no SDK singleton). The
    HTTP client is optional so the composition root can share one
    ``httpx.AsyncClient``; if none is supplied a per-call client is created
    lazily inside each method.
    """

    def __init__(
        self,
        api_key: str,
        *,
        http: Optional[httpx.AsyncClient] = None,
        region: str = "intl",
        model: str = "qwen3-tts-flash",
        default_voice_id: str = "Cherry",
        timeout_s: float = _DEFAULT_TIMEOUT_S,
    ) -> None:
        """Configure the real adapter.

        Args:
            api_key: DashScope bearer token (``QWEN_API_KEY``/``DASHSCOPE_API_KEY``).
            http: Optional shared async HTTP client; one is created per call if
                omitted.
            region: ``"intl"`` (Singapore) or ``"cn"`` (Beijing) — selects the
                base URL.
            model: DashScope TTS model id.
            default_voice_id: Built-in voice used when a request omits one.
            timeout_s: Per-request timeout in seconds.
        """
        self._api_key = api_key
        self._http = http
        self._base_url = _REGION_BASE_URLS.get(region.strip().lower(), _REGION_BASE_URLS["intl"])
        self._model = model
        self._default_voice_id = default_voice_id
        self._timeout_s = timeout_s

    # ------------------------------------------------------------------ #
    # Port surface.
    # ------------------------------------------------------------------ #
    async def synth(self, req: SynthRequest) -> SynthResult:
        """Synthesize ``req.text`` via DashScope and return a validated WAV clip.

        Raises:
            SynthError: On auth / rate_limit / expired_url / malformed_audio /
                timeout — never a raw vendor exception.
        """
        httpx = self._import_httpx()
        text = str(req.text)
        voice = req.voice_id or self._default_voice_id
        payload = self._build_payload(text, voice, req.language)

        client, owns_client = self._client(httpx)
        try:
            try:
                resp = await client.post(
                    f"{self._base_url}{_GENERATION_PATH}",
                    headers=self._auth_headers(),
                    json=payload,
                    timeout=self._timeout_s,
                )
            except httpx.TimeoutException as exc:
                raise SynthError(SynthErrorKind.TIMEOUT, "generation request timed out") from exc
            except httpx.HTTPError as exc:
                raise SynthError(SynthErrorKind.BACKEND, "generation transport error") from exc

            self._raise_for_status(httpx, resp)
            body = self._parse_json(resp)
            wav = await self._resolve_audio(httpx, client, body, req.sample_rate)
        finally:
            if owns_client:
                await client.aclose()

        pcm, _ = wav_to_pcm(wav)
        frames = len(pcm) // SAMPLE_WIDTH_BYTES
        duration_ms = int(round(frames * 1000.0 / req.sample_rate)) if req.sample_rate else 0
        return SynthResult(
            audio=wav,
            duration_ms=duration_ms,
            cost_chars=len(text),
            sample_rate=req.sample_rate,
        )

    async def synth_stream(self, req: SynthRequest) -> AsyncIterator[AudioFrame]:
        """Stream ``req.text`` as cancellable PCM frames over the realtime WS.

        Opens the DashScope realtime WebSocket, sends the text, and reassembles
        ``response.audio.delta`` payloads into :class:`AudioFrame` s. Cancelling
        the iterator (nurse barge-in) closes the socket promptly via the
        ``finally`` block.

        Raises:
            SynthError: On auth / rate_limit / malformed_audio / timeout.
        """
        websockets = self._import_websockets()
        ws_url = self._ws_url()
        seq = 0
        try:
            connection = await websockets.connect(
                ws_url, additional_headers=self._auth_headers()
            )
        except Exception as exc:  # - normalize any connect fault
            raise SynthError(SynthErrorKind.BACKEND, "realtime connect failed") from exc
        try:
            await connection.send(self._ws_session_update(req))
            await connection.send(self._ws_append(str(req.text)))
            await connection.send(self._ws_commit())
            async for raw in connection:
                event = self._decode_ws_event(raw)
                etype = str(event.get("type", ""))
                if etype.endswith("audio.delta"):
                    pcm = base64.b64decode(event.get("delta", "") or "")
                    if pcm:
                        yield AudioFrame(pcm=pcm, seq=seq, is_final=False)
                        seq += 1
                elif etype.endswith("response.done") or etype.endswith("audio.done"):
                    yield AudioFrame(pcm=b"", seq=seq, is_final=True)
                    return
        finally:
            await connection.close()

    async def health(self) -> SynthHealth:
        """Probe the backend; never raises (returns ``ok=False`` on any fault)."""
        try:
            httpx = self._import_httpx()
        except SynthError as exc:
            return SynthHealth(ok=False, mode=IntegrationMode.REAL, detail=str(exc))
        client, owns_client = self._client(httpx)
        try:
            ok = bool(self._api_key)
            detail = "ready" if ok else "missing api key"
            return SynthHealth(ok=ok, mode=IntegrationMode.REAL, detail=detail)
        finally:
            if owns_client:
                await client.aclose()

    # ------------------------------------------------------------------ #
    # Lazy vendor imports (module imports cleanly without the SDK installed).
    # ------------------------------------------------------------------ #
    @staticmethod
    def _import_httpx() -> Any:
        """Import ``httpx`` lazily; map absence to a typed backend error."""
        try:
            import httpx
        except ImportError as exc:  # pragma: no cover - exercised only sans httpx
            raise SynthError(SynthErrorKind.BACKEND, "httpx is not installed") from exc
        return httpx

    @staticmethod
    def _import_websockets() -> Any:
        """Import ``websockets`` lazily; map absence to a typed backend error."""
        try:
            import websockets
        except ImportError as exc:  # pragma: no cover - exercised only sans websockets
            raise SynthError(SynthErrorKind.BACKEND, "websockets is not installed") from exc
        return websockets

    # ------------------------------------------------------------------ #
    # HTTP helpers.
    # ------------------------------------------------------------------ #
    def _client(self, httpx: Any) -> tuple[httpx.AsyncClient, bool]:
        """Return ``(client, owns_client)`` — a shared client or a fresh one."""
        if self._http is not None:
            return self._http, False
        return httpx.AsyncClient(), True

    def _auth_headers(self) -> Dict[str, str]:
        """Build the bearer auth headers (DataInspection left disabled for PHI)."""
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

    def _build_payload(self, text: str, voice: str, language: str) -> Dict[str, Any]:
        """Construct the DashScope generation request body."""
        language_type = _LANGUAGE_TYPE.get(language.strip().lower(), "English")
        return {
            "model": self._model,
            "input": {
                "text": text,
                "voice": voice,
                "language_type": language_type,
            },
        }

    def _raise_for_status(self, httpx: Any, resp: httpx.Response) -> None:
        """Translate non-2xx vendor responses into typed :class:`SynthError`."""
        status = resp.status_code
        if 200 <= status < 300:
            return
        if status == 401 or status == 403:
            raise SynthError(SynthErrorKind.AUTH, "authentication failed")
        if status == 429:
            raise SynthError(
                SynthErrorKind.RATE_LIMIT,
                "rate limited",
                retry_after=self._parse_retry_after(resp.headers.get("Retry-After")),
            )
        raise SynthError(SynthErrorKind.BACKEND, f"backend returned status {status}")

    @staticmethod
    def _parse_retry_after(value: Optional[str]) -> Optional[float]:
        """Parse a numeric ``Retry-After`` header into seconds, if present."""
        if not value:
            return None
        try:
            return float(value)
        except ValueError:
            return None

    @staticmethod
    def _parse_json(resp: httpx.Response) -> Dict[str, Any]:
        """Parse a JSON response body into a dict; reject non-object/garbage."""
        try:
            data = resp.json()
        except ValueError as exc:
            raise SynthError(SynthErrorKind.MALFORMED_AUDIO, "non-JSON generation body") from exc
        if not isinstance(data, dict):
            raise SynthError(SynthErrorKind.MALFORMED_AUDIO, "unexpected generation body")
        return data

    async def _resolve_audio(
        self,
        httpx: Any,
        client: httpx.AsyncClient,
        body: Dict[str, Any],
        sample_rate: int,
    ) -> bytes:
        """Resolve WAV bytes from a URL or inline base64, then validate them."""
        output = body.get("output")
        output = output if isinstance(output, dict) else {}

        inline = self._extract_inline_base64(output)
        if inline is not None:
            wav = self._ensure_wav(inline, sample_rate)
            return wav

        url = self._extract_audio_url(output)
        if url is None:
            raise SynthError(SynthErrorKind.MALFORMED_AUDIO, "no audio url or inline data")

        try:
            audio_resp = await client.get(url, timeout=self._timeout_s)
        except httpx.TimeoutException as exc:
            raise SynthError(SynthErrorKind.TIMEOUT, "audio fetch timed out") from exc
        except httpx.HTTPError as exc:
            raise SynthError(SynthErrorKind.BACKEND, "audio fetch transport error") from exc

        if audio_resp.status_code == 403 or audio_resp.status_code == 404:
            raise SynthError(SynthErrorKind.EXPIRED_URL, "audio url expired or missing")
        if not (200 <= audio_resp.status_code < 300):
            raise SynthError(
                SynthErrorKind.EXPIRED_URL,
                f"audio url returned status {audio_resp.status_code}",
            )
        return self._ensure_wav(audio_resp.content, sample_rate)

    @staticmethod
    def _extract_inline_base64(output: Dict[str, Any]) -> Optional[bytes]:
        """Return decoded inline preview audio bytes, if the response carries them."""
        preview = output.get("preview_audio")
        if isinstance(preview, dict):
            data = preview.get("data")
            if isinstance(data, str) and data:
                try:
                    return base64.b64decode(data)
                except (ValueError, TypeError) as exc:
                    raise SynthError(
                        SynthErrorKind.MALFORMED_AUDIO, "invalid inline base64 audio"
                    ) from exc
        return None

    @staticmethod
    def _extract_audio_url(output: Dict[str, Any]) -> Optional[str]:
        """Return the short-lived audio URL from the generation output, if any."""
        audio = output.get("audio")
        if isinstance(audio, dict):
            url = audio.get("url")
            if isinstance(url, str) and url:
                return url
        return None

    def _ensure_wav(self, audio: bytes, sample_rate: int) -> bytes:
        """Validate ``audio`` is a real PCM WAV; raise typed error otherwise.

        Accepts either a full WAV container or headerless raw PCM (some realtime
        payloads). Raw PCM is wrapped with the requested sample rate. HTML /
        empty / truncated bodies are rejected as ``malformed_audio``.
        """
        if is_valid_pcm_wav(audio):
            return audio
        # Headerless raw PCM: an even, non-trivial byte count we can frame.
        if audio and len(audio) % SAMPLE_WIDTH_BYTES == 0 and audio[:4] != b"RIFF":
            looks_texty = audio[:1] in (b"<", b"{") or b"<html" in audio[:64].lower()
            if not looks_texty:
                wrapped = pcm_to_wav(audio, sample_rate)
                if is_valid_pcm_wav(wrapped):
                    return wrapped
        raise SynthError(SynthErrorKind.MALFORMED_AUDIO, "fetched bytes are not a valid WAV")

    # ------------------------------------------------------------------ #
    # WebSocket helpers.
    # ------------------------------------------------------------------ #
    def _ws_url(self) -> str:
        """Return the region-appropriate realtime WebSocket URL."""
        if self._base_url == _REGION_BASE_URLS["cn"]:
            return "wss://dashscope.aliyuncs.com/api-ws/v1/realtime"
        return "wss://dashscope-intl.aliyuncs.com/api-ws/v1/realtime"

    def _ws_session_update(self, req: SynthRequest) -> str:
        """Serialize the realtime ``session.update`` control message."""
        import json

        voice = req.voice_id or self._default_voice_id
        return json.dumps(
            {
                "type": "session.update",
                "session": {
                    "voice": voice,
                    "response_format": "PCM_24000HZ_MONO_16BIT",
                    "mode": "server_commit",
                },
            }
        )

    @staticmethod
    def _ws_append(text: str) -> str:
        """Serialize the realtime text-append message."""
        import json

        return json.dumps({"type": "input_text_buffer.append", "text": text})

    @staticmethod
    def _ws_commit() -> str:
        """Serialize the realtime commit message."""
        import json

        return json.dumps({"type": "input_text_buffer.commit"})

    @staticmethod
    def _decode_ws_event(raw: Any) -> Dict[str, Any]:
        """Decode a realtime WS frame (str/bytes) into an event dict."""
        import json

        if isinstance(raw, (bytes, bytearray)):
            raw = raw.decode("utf-8", "ignore")
        try:
            event = json.loads(raw)
        except (ValueError, TypeError) as exc:
            raise SynthError(SynthErrorKind.MALFORMED_AUDIO, "invalid realtime frame") from exc
        return event if isinstance(event, dict) else {}

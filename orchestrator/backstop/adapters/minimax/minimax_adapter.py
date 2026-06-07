"""Real MiniMax :class:`ReasoningPort` adapter (chatcompletion_v2 over REST).

ONE responsibility: translate ``ReasoningPort`` calls into MiniMax REST calls
and back. It is the only place in this workstream where vendor I/O lives.

Construction is pure dependency injection: the composition root injects a shared,
connection-pooled ``httpx.AsyncClient`` (with timeouts) and a frozen
:class:`MiniMaxSettings`. The adapter never creates its own client and never
reads ``os.environ``. The ``httpx`` SDK is imported **lazily inside methods** so
this module imports cleanly even when ``httpx`` is absent — a missing SDK never
blocks the contract gate (the test mocks the client).

Wire contract (per the MiniMax platform spec):

* Native route ``POST /text/chatcompletion_v2?GroupId=...`` returns an
  OpenAI-shaped ``choices`` body **plus** a ``base_resp{status_code,status_msg}``
  envelope. A non-zero ``base_resp.status_code`` is an error **even on HTTP
  200** — the adapter checks it explicitly and raises :class:`MiniMaxApiError`.
* OpenAI-compatible route ``POST /chat/completions`` carries no ``base_resp``
  and no ``GroupId``; selected by ``settings.route == "openai"``.

PHI posture: the adapter assumes inbound text is ALREADY redacted (the Service
redacts upstream) and never logs message bodies — only ids, token counts,
finish reasons. Output text is re-wrapped as :class:`RedactedText` (built from
already-redacted inputs, so no PHI is introduced) and every citation is filtered
to the supplied evidence ids, so the model can never fabricate a citation.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from backstop.adapters.minimax._errors import (
    MiniMaxApiError,
    MiniMaxParseError,
    MiniMaxTransportError,
)
from backstop.adapters.minimax._grounding import (
    coerce_dialog_act,
    coerce_route,
    enforce_word_cap,
    safe_fallback_line,
    subset_citations,
)
from backstop.domain.enums import DialogAct, IntegrationMode, RouteDecision
from backstop.domain.redacted import SANCTIONED_TOKEN, RedactedText
from backstop.ports.reasoning_port import (
    ComposeLineRequest,
    ComposeLineResult,
    DenialInterpretation,
    EvidenceSnippet,
    InterpretDenialRequest,
    ReasoningHealth,
)

__all__ = ["MiniMaxSettings", "MiniMaxReasoningAdapter"]

# System contract pinned into every request: the grounding + no-fabrication
# guardrails. Mirrors what the sim enforces structurally.
_COMPOSE_SYSTEM = (
    "You compose ONE short spoken line for a medical-claims appeal phone call. "
    "Use ONLY the numbered evidence provided. Cite the evidence ids you relied "
    "on. Never invent member data, claim numbers, or clinical facts. If the "
    "evidence is insufficient, set grounded=false and emit the safe fallback. "
    'Reply as compact JSON: {"line": str, "dialog_act": str, '
    '"citations": [str], "grounded": bool, "confidence": number}.'
)
_INTERPRET_SYSTEM = (
    "You classify an ambiguous, already-redacted denial reason. Use ONLY the "
    "provided text and code hints; never invent codes or PHI. Reply as compact "
    'JSON: {"category": str, "carc": str|null, "rarc": str|null, '
    '"rebuttal_hook": str, "recommended_route": str, "next_dialog_act": str, '
    '"ambiguous": bool}.'
)

# Native vs OpenAI-compatible route paths (relative to settings.base_url).
_NATIVE_PATH = "/text/chatcompletion_v2"
_OPENAI_PATH = "/chat/completions"

# Finish reasons / flags that mean the model declined or was filtered; we
# downgrade to a safe, ungrounded fallback rather than emitting partial content.
_REFUSAL_FINISH = {"content_filter"}


@dataclass(frozen=True)
class MiniMaxSettings:
    """Frozen, injected configuration for the real MiniMax adapter.

    The composition root builds this from :class:`Settings`; the adapter never
    reads the environment itself.

    Attributes:
        api_key: Bearer token sent as ``Authorization: Bearer <key>``.
        base_url: API base, e.g. ``https://api.minimax.io/v1`` (no trailing /).
        model: MiniMax model id (e.g. ``MiniMax-Text-01``).
        group_id: Account GroupId for the native route query param (optional).
        route: ``"native"`` (chatcompletion_v2) or ``"openai"`` (compat).
        compose_max_tokens: Token budget for the one-line compose call.
        interpret_max_tokens: Token budget for the structured interpret call.
        compose_temperature: Sampling temperature for compose (low).
        interpret_temperature: Sampling temperature for interpret (lowest).
        top_p: Nucleus sampling parameter.
    """

    api_key: str
    base_url: str
    model: str
    group_id: Optional[str] = None
    route: str = "native"
    compose_max_tokens: int = 120
    interpret_max_tokens: int = 400
    compose_temperature: float = 0.2
    interpret_temperature: float = 0.1
    top_p: float = 0.9

    @property
    def is_native(self) -> bool:
        """Return ``True`` when the native chatcompletion_v2 route is selected."""
        return self.route.strip().lower() != "openai"


@dataclass
class MiniMaxReasoningAdapter:
    """``ReasoningPort`` backed by MiniMax's chat-completions REST API.

    Args:
        http: A shared, pre-configured async HTTP client (``httpx.AsyncClient``).
            Typed as ``Any`` so this module imports without ``httpx`` installed.
        settings: Frozen connection/model configuration.
    """

    http: Any
    settings: MiniMaxSettings
    _adapter_name: str = field(default="minimax-real", repr=False)

    # ------------------------------------------------------------------ #
    # compose_line
    # ------------------------------------------------------------------ #
    async def compose_line(self, req: ComposeLineRequest) -> ComposeLineResult:
        """Compose one grounded line via a low-temperature chat completion.

        Renders the grounded-NLG system contract + a serialized evidence/state
        user message, posts it, parses the JSON content, then enforces the same
        guardrails the sim does: word cap, citation-subset, safe fallback on
        refusal/insufficiency.
        """
        allowed_ids = [snip.snippet_id for snip in req.evidence]
        messages = [
            {"role": "system", "content": _COMPOSE_SYSTEM},
            {"role": "user", "content": _compose_user_payload(req)},
        ]
        choice = await self._chat(
            messages,
            max_tokens=self.settings.compose_max_tokens,
            temperature=self.settings.compose_temperature,
        )

        if _is_refusal(choice):
            return self._compose_fallback(req)

        content = _choice_text(choice)
        parsed = _try_json(content)
        if parsed is None:
            # Treat free text as the line itself, but with no usable citations
            # it is not grounded — degrade to the safe fallback.
            return self._compose_fallback(req)

        line = enforce_word_cap(str(parsed.get("line", "")).strip(), req.max_words)
        if not line:
            return self._compose_fallback(req)
        citations = subset_citations(_as_str_list(parsed.get("citations")), allowed_ids)
        grounded = bool(parsed.get("grounded", False)) and bool(citations)
        if not grounded:
            return self._compose_fallback(req)
        act = coerce_dialog_act(
            _opt_str(parsed.get("dialog_act")),
            req.dialog_act or DialogAct.PROVIDE_INFO,
        )
        return ComposeLineResult(
            line=_mint(line),
            dialog_act=act,
            citations=citations,
            grounded=True,
            confidence=_clamp01(_as_float(parsed.get("confidence"), 0.6)),
        )

    def _compose_fallback(self, req: ComposeLineRequest) -> ComposeLineResult:
        """Return the deterministic ungrounded safe-fallback compose result."""
        return ComposeLineResult(
            line=_mint(safe_fallback_line(req.max_words)),
            dialog_act=req.dialog_act or DialogAct.REQUEST_INFO,
            citations=(),
            grounded=False,
            confidence=0.0,
        )

    # ------------------------------------------------------------------ #
    # interpret_denial
    # ------------------------------------------------------------------ #
    async def interpret_denial(
        self, req: InterpretDenialRequest
    ) -> DenialInterpretation:
        """Classify denial text via a classification-temperature completion.

        Parses the structured JSON content into a :class:`DenialInterpretation`,
        coercing route/dialog-act onto the domain enums. On a refusal or an
        unparseable body (after one nudge) it returns an ``ambiguous=True``
        interpretation rather than inventing a classification.
        """
        messages = [
            {"role": "system", "content": _INTERPRET_SYSTEM},
            {"role": "user", "content": _interpret_user_payload(req)},
        ]
        choice = await self._chat(
            messages,
            max_tokens=self.settings.interpret_max_tokens,
            temperature=self.settings.interpret_temperature,
        )
        if _is_refusal(choice):
            return _ambiguous_interpretation(req)

        parsed = _try_json(_choice_text(choice))
        if parsed is None:
            # One bounded "return valid JSON only" nudge, then give up safely.
            nudged = list(messages)
            nudged.append({"role": "user", "content": "Return valid JSON only."})
            retry = await self._chat(
                nudged,
                max_tokens=self.settings.interpret_max_tokens,
                temperature=self.settings.interpret_temperature,
            )
            parsed = _try_json(_choice_text(retry))
            if parsed is None:
                return _ambiguous_interpretation(req)

        category = _opt_str(parsed.get("category")) or "unclassified"
        ambiguous = bool(parsed.get("ambiguous", False)) or category == "unclassified"
        return DenialInterpretation(
            category=category,
            carc=_opt_str(parsed.get("carc")) or req.carc,
            rarc=_opt_str(parsed.get("rarc")) or req.rarc,
            rebuttal_hook=_opt_str(parsed.get("rebuttal_hook"))
            or "Challenge the stated denial basis with the documented record.",
            recommended_route=coerce_route(
                _opt_str(parsed.get("recommended_route")), RouteDecision.APPEAL
            ),
            next_dialog_act=coerce_dialog_act(
                _opt_str(parsed.get("next_dialog_act")), DialogAct.REQUEST_INFO
            ),
            ambiguous=ambiguous,
        )

    # ------------------------------------------------------------------ #
    # health
    # ------------------------------------------------------------------ #
    async def health(self) -> ReasoningHealth:
        """Probe liveness with a cheap GET; never raises.

        Any transport fault is reported as ``ok=False`` with a PHI-free detail
        rather than propagating, so the composition root can probe uniformly.
        """
        try:
            resp = await self.http.get("/models", headers=self._headers())
            ok = 200 <= resp.status_code < 300
            detail = f"http {resp.status_code}"
        except Exception as exc:  # health must never raise; report not-ok
            ok = False
            detail = type(exc).__name__
        return ReasoningHealth(ok=ok, mode=IntegrationMode.REAL, detail=detail)

    # ------------------------------------------------------------------ #
    # Transport
    # ------------------------------------------------------------------ #
    async def _chat(
        self, messages: List[Dict[str, str]], *, max_tokens: int, temperature: float
    ) -> Dict[str, Any]:
        """POST a chat-completion request and return ``choices[0]``.

        Imports ``httpx`` lazily so the module loads without the SDK. Raises
        :class:`MiniMaxTransportError` on a transport fault or non-2xx, and
        :class:`MiniMaxApiError` when the native ``base_resp`` reports failure.
        """
        import httpx  # lazy: module must import even when httpx is absent

        body: Dict[str, Any] = {
            "model": self.settings.model,
            "messages": messages,
            "temperature": temperature,
            "max_completion_tokens": max_tokens,
            "top_p": self.settings.top_p,
            "stream": False,
        }
        path = _NATIVE_PATH if self.settings.is_native else _OPENAI_PATH
        params: Dict[str, str] = {}
        if self.settings.is_native and self.settings.group_id:
            params["GroupId"] = self.settings.group_id

        try:
            resp = await self.http.post(
                path, json=body, params=params, headers=self._headers()
            )
        except httpx.HTTPError as exc:
            raise MiniMaxTransportError(
                f"minimax request failed: {type(exc).__name__}"
            ) from exc

        if resp.status_code >= 300 or resp.status_code < 200:
            raise MiniMaxTransportError(
                f"minimax http {resp.status_code}", status=resp.status_code
            )

        try:
            payload: Dict[str, Any] = resp.json()
        except (ValueError, json.JSONDecodeError) as exc:
            raise MiniMaxParseError("minimax response was not JSON") from exc

        if self.settings.is_native:
            base = payload.get("base_resp") or {}
            status_code = int(base.get("status_code", 0) or 0)
            if status_code != 0:
                raise MiniMaxApiError(status_code, str(base.get("status_msg", "")))

        choices = payload.get("choices")
        if not isinstance(choices, list) or not choices:
            raise MiniMaxParseError("minimax response had no choices")
        first = choices[0]
        if not isinstance(first, dict):
            raise MiniMaxParseError("minimax choice was not an object")
        return first

    def _headers(self) -> Dict[str, str]:
        """Return the auth + content headers (token value is never logged)."""
        return {
            "Authorization": f"Bearer {self.settings.api_key}",
            "Content-Type": "application/json",
        }


# --------------------------------------------------------------------------- #
# Pure helpers (no I/O).
# --------------------------------------------------------------------------- #
def _compose_user_payload(req: ComposeLineRequest) -> str:
    """Serialize the compose request's evidence + state into a user message."""
    lines = [
        f"CALL_STATE: {req.call_state!s}",
        f"MAX_WORDS: {req.max_words}",
    ]
    if req.dialog_act is not None:
        lines.append(f"DESIRED_DIALOG_ACT: {req.dialog_act.value}")
    lines.append("EVIDENCE (cite only these ids):")
    lines.extend(_format_evidence(req.evidence))
    return "\n".join(lines)


def _interpret_user_payload(req: InterpretDenialRequest) -> str:
    """Serialize the interpret request into a user message."""
    lines = [f"DENIAL_TEXT: {req.denial_text!s}"]
    if req.carc:
        lines.append(f"CARC_HINT: {req.carc}")
    if req.rarc:
        lines.append(f"RARC_HINT: {req.rarc}")
    return "\n".join(lines)


def _format_evidence(evidence: Tuple[EvidenceSnippet, ...]) -> List[str]:
    """Render evidence snippets as ``- <id>: <text>`` lines."""
    return [f"- {snip.snippet_id}: {snip.text!s}" for snip in evidence]


def _choice_text(choice: Dict[str, Any]) -> str:
    """Extract ``choice.message.content`` as a string (empty when absent)."""
    message = choice.get("message")
    if not isinstance(message, dict):
        return ""
    content = message.get("content")
    return content if isinstance(content, str) else ""


def _is_refusal(choice: Dict[str, Any]) -> bool:
    """Return ``True`` when the choice signals a content-filter refusal."""
    finish = choice.get("finish_reason")
    return isinstance(finish, str) and finish in _REFUSAL_FINISH


def _try_json(content: str) -> Optional[Dict[str, Any]]:
    """Parse a JSON object out of ``content`` (tolerating code fences/prose).

    Returns ``None`` when no JSON object can be recovered, so callers can apply
    their fallback path rather than raising.
    """
    text = content.strip()
    if not text:
        return None
    # Strip a leading ```json fence if present.
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end < start:
        return None
    candidate = text[start : end + 1]
    try:
        loaded = json.loads(candidate)
    except (ValueError, json.JSONDecodeError):
        return None
    return loaded if isinstance(loaded, dict) else None


def _ambiguous_interpretation(req: InterpretDenialRequest) -> DenialInterpretation:
    """Return the safe ``ambiguous=True`` interpretation for ``req``."""
    return DenialInterpretation(
        category="unclassified",
        carc=req.carc,
        rarc=req.rarc,
        rebuttal_hook="Clarify the exact denial reason before selecting a rebuttal angle.",
        recommended_route=RouteDecision.APPEAL,
        next_dialog_act=DialogAct.REQUEST_INFO,
        ambiguous=True,
    )


def _as_str_list(value: object) -> List[str]:
    """Coerce ``value`` into a list of strings (non-strings dropped)."""
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def _opt_str(value: object) -> Optional[str]:
    """Return ``value`` as a non-empty string, else ``None``."""
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _as_float(value: object, default: float) -> float:
    """Coerce ``value`` into a float, falling back to ``default``."""
    if isinstance(value, bool):
        return default
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return default
    return default


def _clamp01(value: float) -> float:
    """Clamp ``value`` into the closed unit interval ``[0, 1]``."""
    return max(0.0, min(1.0, value))


def _mint(text: str) -> RedactedText:
    """Re-wrap an already-redacted output ``text`` as :class:`RedactedText`.

    The output is built only from already-redacted inputs + static templates, so
    minting through the sanctioned factory introduces no PHI; spans are empty
    because original offsets do not map onto the composed string.
    """
    return RedactedText.from_redaction(text, SANCTIONED_TOKEN, spans=())

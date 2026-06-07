"""TrueFoundryRedactionAdapter — real guardrail wrapper over the local scrubber.

TrueFoundry's platform offers hosted PII/PHI Guardrails, but Backstop does NOT
depend on them being enabled in the sandbox: redaction must be real even against
a bare OpenAI-compatible endpoint. So this "real" adapter is a thin wrapper that

* always runs the real local :class:`LocalRedactionAdapter` (the sole, vendor-free
  minter of ``RedactedText``), and
* OPTIONALLY consults the hosted guardrail as a best-effort enrichment pass when a
  client + key are configured.

If the hosted call is unavailable (no SDK, no key, network error, non-2xx) the
adapter silently falls back to the purely local result — it never weakens
redaction and never emits raw text. The vendor SDK is imported LAZILY inside the
enrichment method so the module imports cleanly when the SDK is absent.

Because the local pass already masks every category the hosted guardrail would,
the fallback is loss-free for the redaction contract; the hosted pass can only add
masks, never remove them.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, List, Optional

from backstop.adapters.truefoundry.local_redaction_adapter import LocalRedactionAdapter
from backstop.domain.redacted import RedactedText
from backstop.ports.redaction_port import Message, RedactedMessage

if TYPE_CHECKING:  # pragma: no cover - typing only
    from backstop.infra.config import Settings

__all__ = ["TrueFoundryRedactionAdapter"]


class TrueFoundryRedactionAdapter:
    """Real guardrail wrapper that degrades to the local scrubber.

    Implements :class:`~backstop.ports.redaction_port.RedactionPort`. The local
    adapter does all minting of ``RedactedText``; the hosted guardrail (when
    reachable) is consulted first to catch anything the regex ruleset might miss,
    and its output is then re-scrubbed locally so the egress-safe newtype is
    always produced by the sanctioned local path.
    """

    def __init__(
        self,
        *,
        local: Optional[LocalRedactionAdapter] = None,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
    ) -> None:
        """Wrap a local scrubber and remember optional hosted-guardrail config."""
        self._local = local if local is not None else LocalRedactionAdapter()
        self._api_key = api_key
        self._base_url = base_url

    @classmethod
    def from_settings(
        cls,
        settings: Settings,
        *,
        local: Optional[LocalRedactionAdapter] = None,
    ) -> TrueFoundryRedactionAdapter:
        """Build from frozen :class:`Settings` (TrueFoundry key + base URL)."""
        return cls(
            local=local,
            api_key=settings.truefoundry_api_key,
            base_url=settings.truefoundry_base_url,
        )

    def redact_text(self, text: str) -> RedactedText:
        """Best-effort hosted enrichment, then mint via the local scrubber."""
        enriched = self._guardrail_or_passthrough(text)
        return self._local.redact_text(enriched)

    def redact_messages(self, msgs: List[Message]) -> List[RedactedMessage]:
        """Redact every message, preserving order and roles."""
        return [
            RedactedMessage(role=m.role, content=self.redact_text(m.content))
            for m in msgs
        ]

    def contains_phi(self, text: str) -> bool:
        """Delegate the defence-in-depth predicate to the local detector."""
        return self._local.contains_phi(text)

    # ----------------------------------------------------------------- #
    # Best-effort hosted guardrail (never raises; lazy SDK import).
    # ----------------------------------------------------------------- #
    def _guardrail_or_passthrough(self, text: str) -> str:
        """Return hosted-masked text, or the input unchanged on any failure.

        The hosted guardrail is enrichment only. The local scrubber runs after
        this regardless, so a failure here can never let PHI through — it just
        means the local ruleset alone does the masking.
        """
        if not self._api_key:
            return text
        try:
            return self._call_guardrail(text)
        except Exception:  # - degrade to local on ANY vendor fault
            return text

    def _call_guardrail(self, text: str) -> str:
        """Invoke the hosted guardrail via a lazily-imported client.

        The SDK import lives here so the module imports cleanly without the
        vendor package installed. Returns hosted-masked text on success.
        """
        import httpx  # - lazy: real adapter is the only I/O site

        if not self._base_url:
            return text
        timeout = httpx.Timeout(connect=5.0, read=10.0, write=10.0, pool=5.0)
        with httpx.Client(timeout=timeout) as client:
            resp = client.post(
                f"{self._base_url}/api/guardrails/pii/mask",
                headers={"Authorization": f"Bearer {self._api_key}"},
                json={"text": text},
            )
            resp.raise_for_status()
            payload = resp.json()
        masked = payload.get("masked_text")
        return masked if isinstance(masked, str) and masked else text

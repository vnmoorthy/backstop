"""SimGatewayAdapter — the offline LLM chokepoint with GENUINE local work.

Implements :class:`~backstop.ports.llm_gateway_port.LLMGatewayPort`. It is NOT an
echo: it is injected the SAME ``RedactionPort`` / ``AuditLogPort`` /
``CostLedgerPort`` singletons as the real adapter, so redaction, the tamper-evident
hash-chained audit, and the priced cost ledger are 100% real in sim — only the
upstream model proxy is stubbed by the local, seeded
:class:`LocalCompletionEngine`.

complete() flow: redact-out (already redacted at the boundary, re-asserted) ->
locally compose a stage-appropriate completion -> redact-in -> price -> audit ->
PHI-free response. stream() yields the locally-composed line in token groups with
the same redact-in/audit/cost tail on completion.

Stdlib + domain + sibling adapters only; no vendor SDK, no network.
"""

from __future__ import annotations

from typing import AsyncIterator, Optional

from backstop.adapters.truefoundry.gateway_common import (
    chunk_words,
    finalize_call,
)
from backstop.adapters.truefoundry.local_completion_engine import LocalCompletionEngine
from backstop.domain.enums import IntegrationMode
from backstop.domain.redacted import RedactedText
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

__all__ = ["SimGatewayAdapter"]

_DEFAULT_SIM_MODEL = "sim/local-composer"


class SimGatewayAdapter:
    """Offline LLM chokepoint backed by a deterministic local composer."""

    def __init__(
        self,
        *,
        redaction: RedactionPort,
        audit: AuditLogPort,
        cost: CostLedgerPort,
        engine: Optional[LocalCompletionEngine] = None,
        default_model: str = _DEFAULT_SIM_MODEL,
    ) -> None:
        """Inject the shared redaction/audit/cost singletons and local engine."""
        self._redaction = redaction
        self._audit = audit
        self._cost = cost
        self._engine = engine if engine is not None else LocalCompletionEngine()
        self._default_model = default_model

    @property
    def mode(self) -> IntegrationMode:
        """This adapter always reports ``sim`` (the honesty contract)."""
        return IntegrationMode.SIM

    # ----------------------------------------------------------------- #
    # LLMGatewayPort.
    # ----------------------------------------------------------------- #
    async def complete(self, req: LLMRequest) -> LLMResponse:
        """Compose locally, then run the shared redact-in/audit/cost tail."""
        model = req.model or self._default_model
        redacted_prompt = self._flatten(req)
        # Re-assert outbound redaction (defence-in-depth) before the engine sees it.
        safe_prompt = self._redaction.redact_text(redacted_prompt).text
        raw_completion = self._engine.compose(
            appeal_id=req.appeal_id, stage=req.stage, prompt=safe_prompt
        )
        return finalize_call(
            redaction=self._redaction,
            audit=self._audit,
            cost=self._cost,
            mode=IntegrationMode.SIM,
            appeal_id=req.appeal_id,
            stage=req.stage,
            model=model,
            redacted_prompt_text=safe_prompt,
            raw_completion=raw_completion,
            finish_reason="stop",
            gateway_request_id=None,
        )

    async def stream(self, req: LLMRequest) -> AsyncIterator[LLMChunk]:
        """Stream the locally-composed line in redacted token groups.

        Each flushed group is redacted before it is yielded so PHI never streams
        unredacted; the final empty-delta chunk carries ``finish_reason`` and the
        call is accounted (audit + cost) via the shared tail.
        """
        model = req.model or self._default_model
        redacted_prompt = self._flatten(req)
        safe_prompt = self._redaction.redact_text(redacted_prompt).text
        raw_completion = self._engine.compose(
            appeal_id=req.appeal_id, stage=req.stage, prompt=safe_prompt
        )
        for group in chunk_words(raw_completion, group=6):
            delta = self._redaction.redact_text(group)
            yield LLMChunk(delta=delta, finish_reason=None)
        # Account the full call once at the end (same tail as complete()).
        finalize_call(
            redaction=self._redaction,
            audit=self._audit,
            cost=self._cost,
            mode=IntegrationMode.SIM,
            appeal_id=req.appeal_id,
            stage=req.stage,
            model=model,
            redacted_prompt_text=safe_prompt,
            raw_completion=raw_completion,
            finish_reason="stop",
            gateway_request_id=None,
        )
        yield LLMChunk(delta=self._empty(), finish_reason="stop")

    def health(self) -> GatewayHealth:
        """Return a sim liveness snapshot (always reachable)."""
        return GatewayHealth(
            ok=True,
            mode=IntegrationMode.SIM,
            default_model=self._default_model,
            detail="local composer (no upstream)",
        )

    def cost_to_date(self, appeal_id: Optional[str] = None) -> GatewayCostSnapshot:
        """Read aggregated spend through the shared cost ledger."""
        snap = self._cost.snapshot(appeal_id)
        return GatewayCostSnapshot(
            total=snap.total, appeal_id=snap.appeal_id, by_stage=dict(snap.by_stage)
        )

    # ----------------------------------------------------------------- #
    # Helpers.
    # ----------------------------------------------------------------- #
    @staticmethod
    def _flatten(req: LLMRequest) -> str:
        """Flatten the redacted request messages into one prompt string."""
        return "\n".join(f"{m.role}: {m.content.text}" for m in req.messages)

    def _empty(self) -> RedactedText:
        """Return an empty, sanctioned ``RedactedText`` for the terminal chunk."""
        return self._redaction.redact_text("")

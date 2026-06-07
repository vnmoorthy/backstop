"""LLMGatewayPort — TrueFoundry single LLM chokepoint (L2 port).

Every model byte flows through this port: redact-out, upstream, redact-in,
hash-chained audit append, priced cost record, PHI-free response. Because it is
an egress port, all PHI-bearing message content is typed ``RedactedText`` so an
unredacted prompt is a type error at the boundary. Real and sim adapters inject
the same redaction/audit/cost singletons.

This module defines the Protocol plus its request/result DTOs only; concrete
adapters live in ``backstop.adapters.truefoundry``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import AsyncIterator, Dict, Optional, Protocol, Tuple, runtime_checkable

from backstop.domain.enums import IntegrationMode
from backstop.domain.money import Money
from backstop.domain.redacted import RedactedText

__all__ = [
    "GatewayMessage",
    "LLMRequest",
    "LLMResponse",
    "LLMChunk",
    "GatewayHealth",
    "GatewayCostSnapshot",
    "LLMGatewayPort",
]


@dataclass(frozen=True)
class GatewayMessage:
    """One chat message whose content is already redacted (egress-safe).

    Attributes:
        role: The OpenAI-style role (``system`` / ``user`` / ``assistant``).
        content: The message body, already passed through ``RedactionPort``.
    """

    role: str
    content: RedactedText


@dataclass(frozen=True)
class LLMRequest:
    """A redacted completion request scoped to an appeal and pipeline stage.

    Attributes:
        appeal_id: Surrogate appeal identifier for audit/cost attribution.
        stage: Pipeline stage label (e.g. ``synthesize_rebuttal``).
        messages: Redacted chat messages forming the prompt.
        model: Optional model override; defaults to the configured model.
        max_tokens: Optional completion cap.
        temperature: Sampling temperature.
    """

    appeal_id: str
    stage: str
    messages: Tuple[GatewayMessage, ...]
    model: Optional[str] = None
    max_tokens: Optional[int] = None
    temperature: float = 0.0


@dataclass(frozen=True)
class LLMResponse:
    """A PHI-free completion plus accounting metadata.

    Attributes:
        text: The redacted completion text (re-redacted on the inbound leg).
        model: The model that produced the completion.
        prompt_tokens: Counted prompt tokens.
        completion_tokens: Counted completion tokens.
        cost: Priced cost recorded for this call.
        finish_reason: Upstream finish reason, when reported.
        gateway_request_id: Upstream correlation id, when reported.
    """

    text: RedactedText
    model: str
    prompt_tokens: int
    completion_tokens: int
    cost: Money
    finish_reason: Optional[str] = None
    gateway_request_id: Optional[str] = None


@dataclass(frozen=True)
class LLMChunk:
    """One streamed completion delta.

    Attributes:
        delta: The redacted incremental text for this chunk.
        finish_reason: Set on the terminal chunk, otherwise ``None``.
    """

    delta: RedactedText
    finish_reason: Optional[str] = None


@dataclass(frozen=True)
class GatewayHealth:
    """Liveness snapshot for the gateway (never raises).

    Attributes:
        ok: Whether the gateway is reachable and serving.
        mode: Whether the active adapter is real or sim.
        default_model: The default model the gateway routes to.
        detail: Optional human-readable status detail.
    """

    ok: bool
    mode: IntegrationMode
    default_model: str
    detail: Optional[str] = None


@dataclass(frozen=True)
class GatewayCostSnapshot:
    """Aggregated gateway spend, optionally scoped to one appeal.

    Attributes:
        total: Total priced cost across the included records.
        appeal_id: The appeal this snapshot is scoped to, or ``None`` for all.
        by_stage: Per-stage cost breakdown.
    """

    total: Money
    appeal_id: Optional[str] = None
    by_stage: Dict[str, Money] = field(default_factory=dict)


@runtime_checkable
class LLMGatewayPort(Protocol):
    """The single redacted LLM chokepoint for every model call."""

    async def complete(self, req: LLMRequest) -> LLMResponse:
        """Run a completion through redact/audit/cost; return a PHI-free response.

        Raises:
            GatewayError: On upstream non-2xx after bounded retry.
        """
        ...

    def stream(self, req: LLMRequest) -> AsyncIterator[LLMChunk]:
        """Stream a completion as redacted chunks through the same chokepoint.

        Raises:
            GatewayError: On upstream non-2xx after bounded retry.
        """
        ...

    def health(self) -> GatewayHealth:
        """Return a liveness snapshot; never raises."""
        ...

    def cost_to_date(self, appeal_id: Optional[str] = None) -> GatewayCostSnapshot:
        """Return aggregated spend, scoped to ``appeal_id`` when provided."""
        ...

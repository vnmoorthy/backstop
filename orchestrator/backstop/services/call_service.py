"""CallService — the per-turn PAVO loop use-case.

One ``handle_turn`` is the hot path of a payer call. The load-bearing ordering,
fixed by this service and asserted by its tests:

1. :meth:`RoutingPort.route` runs **exactly once** and **before** any
   reasoning / synthesis / transport — the single per-turn tier gate.
2. ``ONDEVICE_FAST`` turns **never** call :class:`ReasoningPort` (the cost
   collapse): the fast path emits a deterministic, locally-composed line.
3. Only on a denial turn does the service retrieve evidence and compose a
   grounded line through the gateway-backed reasoner.
4. Every outbound text crosses the egress boundary as ``RedactedText`` (it is
   produced through the injected :class:`RedactionPort`), is synthesized to a
   WAV, and is published as a redacted event.

The service depends only on ports; it never imports a concrete adapter, never
touches the clock except through :class:`ClockPort`, and never constructs
``RedactedText`` itself (the redaction port is the sole producer).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple

from backstop.domain.enums import DialogAct, ModelTier, Speaker
from backstop.domain.models import CallTurn
from backstop.domain.redacted import RedactedText
from backstop.ports.clock_port import ClockPort
from backstop.ports.event_bus_port import EventBusPort, RedactedEvent
from backstop.ports.reasoning_port import (
    ComposeLineRequest,
    EvidenceSnippet,
    ReasoningPort,
)
from backstop.ports.redaction_port import RedactionPort
from backstop.ports.retrieval_port import RetrievalPort, RetrievalQuery
from backstop.ports.routing_port import RoutingPort
from backstop.ports.speech_synthesis_port import SpeechSynthesisPort, SynthRequest

__all__ = ["TurnInput", "TurnResult", "CallService"]


@dataclass(frozen=True)
class TurnInput:
    """The redacted, non-PHI context for one turn of a payer call.

    Attributes:
        call_id: Surrogate call identifier (non-PHI).
        appeal_id: The appeal this call advances.
        turn: The speaker + 12-dim telemetry observation for this turn.
        call_state: A redacted summary of the conversation so far.
        is_denial_turn: Whether this turn is the denial-reason turn (the only
            turn that retrieves evidence and composes a grounded rebuttal).
        carc: Optional CARC code to narrow retrieval (non-PHI).
        payer_id: Optional payer surrogate to narrow retrieval (non-PHI).
        voice_id: Brand voice for synthesis.
    """

    call_id: str
    appeal_id: str
    turn: CallTurn
    call_state: RedactedText
    is_denial_turn: bool = False
    carc: Optional[str] = None
    payer_id: Optional[str] = None
    voice_id: str = "backstop-default"


@dataclass(frozen=True)
class TurnResult:
    """The outcome of handling one turn.

    Attributes:
        tier: The tier the router selected for this turn (the one routing call).
        line: The redacted spoken line that was synthesized and published.
        dialog_act: The dialog act the line realizes.
        citations: Evidence ids cited by the line (empty on the fast path).
        grounded: Whether the line was grounded in retrieved evidence.
        audio: The synthesized WAV bytes for the line.
        used_reasoning: Whether :class:`ReasoningPort` was consulted this turn.
    """

    tier: ModelTier
    line: RedactedText
    dialog_act: DialogAct
    citations: Tuple[str, ...]
    grounded: bool
    audio: bytes
    used_reasoning: bool


# Deterministic on-device line used when the router collapses to the fast tier.
_FAST_LINE_TEXT = "One moment please while I review this claim."


class CallService:
    """Drive one PAVO-routed conversational turn over injected ports."""

    def __init__(
        self,
        *,
        routing: RoutingPort,
        retrieval: RetrievalPort,
        reasoning: ReasoningPort,
        speech: SpeechSynthesisPort,
        redaction: RedactionPort,
        events: EventBusPort,
        clock: ClockPort,
        max_words: int = 40,
    ) -> None:
        """Store the per-turn collaborators; all are ports, never adapters."""
        self._routing = routing
        self._retrieval = retrieval
        self._reasoning = reasoning
        self._speech = speech
        self._redaction = redaction
        self._events = events
        self._clock = clock
        self._max_words = max_words

    async def handle_turn(self, turn_input: TurnInput) -> TurnResult:
        """Handle one turn: route once, (maybe) reason, synthesize, publish.

        ``route()`` is called exactly once and before any other capability. On
        ``ONDEVICE_FAST`` the reasoner is skipped entirely. The composed line is
        always redacted before synthesis/publish.
        """
        # 1. The single per-turn tier gate — exactly one route() call, first.
        decision = self._routing.route(turn_input.turn.observation)
        tier = decision.tier

        if tier is ModelTier.ONDEVICE_FAST:
            # Cost collapse: never touch the reasoner on the fast path.
            line = self._redaction.redact_text(_FAST_LINE_TEXT)
            dialog_act = DialogAct.PROVIDE_INFO
            citations: Tuple[str, ...] = ()
            grounded = False
            used_reasoning = False
        else:
            line, dialog_act, citations, grounded = await self._compose(turn_input)
            used_reasoning = True

        # Egress: synthesize the redacted line and publish a redacted event.
        synth = await self._speech.synth(
            SynthRequest(text=line, voice_id=turn_input.voice_id)
        )
        await self._publish(turn_input, dialog_act, line)

        return TurnResult(
            tier=tier,
            line=line,
            dialog_act=dialog_act,
            citations=citations,
            grounded=grounded,
            audio=synth.audio,
            used_reasoning=used_reasoning,
        )

    async def _compose(
        self, turn_input: TurnInput
    ) -> Tuple[RedactedText, DialogAct, Tuple[str, ...], bool]:
        """Retrieve evidence (denial turn only) and compose a grounded line."""
        evidence: Tuple[EvidenceSnippet, ...] = ()
        if turn_input.is_denial_turn:
            result = await self._retrieval.retrieve(
                RetrievalQuery(
                    text=self._query_text(turn_input),
                    carc=turn_input.carc,
                    payer_id=turn_input.payer_id,
                )
            )
            evidence = tuple(
                EvidenceSnippet(
                    snippet_id=chunk.chunk_id,
                    text=self._redaction.redact_text(chunk.text),
                    score=chunk.score,
                )
                for chunk in result.chunks
            )

        composed = await self._reasoning.compose_line(
            ComposeLineRequest(
                call_state=turn_input.call_state,
                evidence=evidence,
                max_words=self._max_words,
            )
        )
        return (
            composed.line,
            composed.dialog_act,
            composed.citations,
            composed.grounded,
        )

    @staticmethod
    def _query_text(turn_input: TurnInput) -> str:
        """Build a PHI-free retrieval query from non-PHI denial context."""
        parts = ["denial rebuttal"]
        if turn_input.carc:
            parts.append(f"carc {turn_input.carc}")
        if turn_input.payer_id:
            parts.append(f"payer {turn_input.payer_id}")
        return " ".join(parts)

    async def _publish(
        self,
        turn_input: TurnInput,
        dialog_act: DialogAct,
        line: RedactedText,
    ) -> None:
        """Publish the redacted line to the call's event channel."""
        event = RedactedEvent(
            kind=f"line_composed:{dialog_act.value}",
            body=line,
            seq_iso=self._clock.now().isoformat(),
        )
        channel = f"call:{turn_input.call_id}"
        await self._events.publish(channel, event)

    @staticmethod
    def is_agent_turn(turn: CallTurn) -> bool:
        """Return whether ``turn`` is spoken by the Backstop agent."""
        return turn.speaker is Speaker.AGENT

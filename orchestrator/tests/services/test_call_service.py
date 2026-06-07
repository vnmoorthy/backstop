"""Tests for :class:`CallService` — the per-turn PAVO loop.

Pins the load-bearing per-turn invariants against in-test fake ports:

* ``route()`` is called exactly once, and **before** any reasoning / retrieval /
  synthesis / publish.
* ``ONDEVICE_FAST`` collapses cost: :class:`ReasoningPort` is never consulted.
* ``CLOUD_PREMIUM`` and ``HYBRID_BALANCED`` compose through the reasoner; on the
  denial turn evidence is retrieved first.
* Every outbound text crosses the boundary as ``RedactedText`` (the published
  event body and the synth request text are both ``RedactedText``).
"""

from __future__ import annotations

import pytest

from backstop.domain.enums import ModelTier, Speaker
from backstop.domain.models import CallTurn
from backstop.domain.redacted import RedactedText
from backstop.services.call_service import CallService, TurnInput
from tests.services.fakes import (
    CallLog,
    FakeClock,
    FakeRedaction,
    RecordingEventBus,
    RecordingReasoning,
    RecordingRetrieval,
    RecordingRouter,
    RecordingSpeech,
    make_observation,
    make_redacted,
)


def _service(tier: ModelTier, log: CallLog) -> tuple[CallService, dict]:
    """Build a CallService wired to fakes sharing one ordering ``log``."""
    parts = {
        "routing": RecordingRouter(tier, log),
        "retrieval": RecordingRetrieval(log),
        "reasoning": RecordingReasoning(log),
        "speech": RecordingSpeech(log),
        "redaction": FakeRedaction(log),
        "events": RecordingEventBus(log),
        "clock": FakeClock(),
    }
    service = CallService(
        routing=parts["routing"],
        retrieval=parts["retrieval"],
        reasoning=parts["reasoning"],
        speech=parts["speech"],
        redaction=parts["redaction"],
        events=parts["events"],
        clock=parts["clock"],
    )
    return service, parts


def _turn_input(*, is_denial_turn: bool = False) -> TurnInput:
    """Build a redacted turn input."""
    return TurnInput(
        call_id="call-1",
        appeal_id="appeal-1",
        turn=CallTurn(
            index=0,
            speaker=Speaker.AGENT,
            observation=make_observation(),
        ),
        call_state=make_redacted("greeting context"),
        is_denial_turn=is_denial_turn,
        carc="197",
        payer_id="payer-1",
    )


async def test_route_called_exactly_once_and_first() -> None:
    """``route`` runs once and strictly before reasoning/synth/publish."""
    log: CallLog = []
    service, parts = _service(ModelTier.CLOUD_PREMIUM, log)

    await service.handle_turn(_turn_input(is_denial_turn=True))

    assert parts["routing"].route_calls == 1
    # route is the first recorded cross-port action.
    assert log[0] == "route"
    # nothing in {retrieve, compose, synth, publish} precedes route.
    route_idx = log.index("route")
    for marker in ("retrieve", "compose", "synth", "publish"):
        assert log.index(marker) > route_idx


async def test_ondevice_fast_does_not_call_reasoning() -> None:
    """The fast tier collapses cost: the reasoner is never consulted."""
    log: CallLog = []
    service, parts = _service(ModelTier.ONDEVICE_FAST, log)

    result = await service.handle_turn(_turn_input(is_denial_turn=True))

    assert parts["reasoning"].compose_calls == 0
    assert parts["retrieval"].retrieve_calls == 0
    assert result.used_reasoning is False
    assert result.tier is ModelTier.ONDEVICE_FAST
    assert "compose" not in log and "retrieve" not in log


async def test_cloud_premium_composes_through_reasoner() -> None:
    """A non-fast tier composes the line through the reasoner."""
    log: CallLog = []
    service, parts = _service(ModelTier.CLOUD_PREMIUM, log)

    result = await service.handle_turn(_turn_input(is_denial_turn=True))

    assert parts["reasoning"].compose_calls == 1
    assert result.used_reasoning is True
    assert result.grounded is True
    assert result.citations == ("runbook-1",)


async def test_denial_turn_retrieves_before_composing() -> None:
    """On the denial turn, retrieval precedes composition."""
    log: CallLog = []
    service, parts = _service(ModelTier.HYBRID_BALANCED, log)

    await service.handle_turn(_turn_input(is_denial_turn=True))

    assert parts["retrieval"].retrieve_calls == 1
    assert log.index("retrieve") < log.index("compose")
    # The retrieval query is built from non-PHI denial context only.
    query = parts["retrieval"].last_query
    assert query is not None
    assert query.carc == "197"
    assert "MEMBER123" not in query.text


async def test_non_denial_turn_skips_retrieval() -> None:
    """A non-denial turn composes without retrieving evidence."""
    log: CallLog = []
    service, parts = _service(ModelTier.CLOUD_PREMIUM, log)

    result = await service.handle_turn(_turn_input(is_denial_turn=False))

    assert parts["retrieval"].retrieve_calls == 0
    assert parts["reasoning"].compose_calls == 1
    assert result.citations == ()


async def test_every_outbound_text_is_redacted_text() -> None:
    """The synth request text and published event body are ``RedactedText``."""
    log: CallLog = []
    service, parts = _service(ModelTier.ONDEVICE_FAST, log)

    result = await service.handle_turn(_turn_input())

    assert isinstance(result.line, RedactedText)
    # Synthesis received RedactedText.
    assert len(parts["speech"].requests) == 1
    assert isinstance(parts["speech"].requests[0].text, RedactedText)
    # The published event body is RedactedText.
    assert len(parts["events"].published) == 1
    _channel, event = parts["events"].published[0]
    assert isinstance(event.body, RedactedText)


@pytest.mark.parametrize(
    "tier",
    [ModelTier.CLOUD_PREMIUM, ModelTier.HYBRID_BALANCED, ModelTier.ONDEVICE_FAST],
)
async def test_route_once_for_every_tier(tier: ModelTier) -> None:
    """Whatever tier is selected, exactly one route call is made."""
    log: CallLog = []
    service, parts = _service(tier, log)

    await service.handle_turn(_turn_input(is_denial_turn=True))

    assert parts["routing"].route_calls == 1

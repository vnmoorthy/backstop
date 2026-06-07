"""The per-turn call loop.

For each inbound IVR/rep utterance the calling agent must handle, we:
  1. extract the 12-dim signal from the turn (complexity, SNR, context)
  2. route it through the masked PAVO router -> a model tier
  3. accrue the cost (PAVO vs the all-frontier counterfactual)
  4. emit events (call.turn, pavo.route, cost.tick) for the dashboard

The model tier that PAVO selects is what would *compose the agent's reply* in the
full system (local model for "press 3 / please hold", frontier only on the turn
where the rep states the denial reason). H3 wires the routing + cost spine; the
reply composition (MiniMax/Qwen) and Moss retrieval are layered in H4.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

from .cost import CostLedger
from .ivr_sim import Scenario
from .models import CallTurn
from .pavo.router import MaskedPAVORouter, RouteDecision
from .pavo.signal import turn_to_state

EmitFn = Optional[Callable[[str, dict], None]]


@dataclass
class CallResult:
    agent_id: str
    transcript: list[CallTurn]
    decisions: list[RouteDecision]
    ledger: CostLedger


def run_call(scenario: Scenario, router: MaskedPAVORouter, emit: EmitFn = None) -> CallResult:
    ledger = CostLedger()
    decisions: list[RouteDecision] = []

    def _emit(t: str, p: dict) -> None:
        if emit:
            emit(t, {"agent_id": scenario.agent_id, **p})

    for turn in scenario.turns:
        _emit("call.turn", {"turn": turn.to_dict()})
        # token estimate scales with context; cheap turns are short
        tokens = max(40, turn.ctx_tokens)
        d = router.route(turn_to_state(turn), turn_id=turn.turn_id, tokens=tokens)
        ledger.add(d)
        decisions.append(d)
        _emit("pavo.route", {**d.to_dict(), "is_denial_reason": turn.is_denial_reason})
        _emit("cost.tick", ledger.snapshot())

    return CallResult(scenario.agent_id, list(scenario.turns), decisions, ledger)

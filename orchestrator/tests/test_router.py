"""Tests for the masked PAVO router — the verified routing core.

Covers SPEC.md acceptance criteria #1 and #2:
  #1 complexity 1 and 5 route to DIFFERENT tiers (escalation)
  #2 WITHOUT the mask the router is constant (documents why the mask exists)

Runnable two ways:
    cd orchestrator && python -m pytest tests/test_router.py -q
    cd orchestrator && python tests/test_router.py
"""
from __future__ import annotations

import sys
from pathlib import Path

# make `backstop` importable when run directly
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backstop.models import CallTurn
from backstop.pavo import MaskedPAVORouter, turn_to_state


def _route_complexity(router, c, snr=42.0):
    turn = CallTurn(turn_id=c, speaker="rep", text="...", complexity=c, snr_db=snr, ctx_tokens=200)
    return router.route(turn_to_state(turn), turn_id=c, apply_mask=True)


def test_weights_load():
    r = MaskedPAVORouter()
    assert r.n_params == 85041, f"expected 85,041 params, got {r.n_params}"


def test_masked_routing_escalates():
    r = MaskedPAVORouter()
    decisions = [_route_complexity(r, c) for c in range(1, 6)]
    idxs = [d.profile_idx for d in decisions]
    lats = [d.latency_factor for d in decisions]
    feas = [d.feasible_count for d in decisions]

    # profile index escalates monotonically with complexity
    assert idxs == sorted(idxs), f"profiles not escalating: {idxs}"
    assert idxs[-1] > idxs[0], f"complexity 5 must escalate past complexity 1: {idxs}"
    # latency factor (cost proxy) rises with complexity
    assert lats[-1] > lats[0], f"latency factor must rise: {lats}"
    # feasible set shrinks as complexity rises (the mask doing its job)
    assert feas[0] > feas[-1], f"feasible set must shrink: {feas}"
    # the cheap turn lands on the near-free local tier; the hard turn on frontier
    assert decisions[0].tier == "local_fast", f"complexity-1 tier: {decisions[0].tier}"
    assert decisions[-1].tier == "frontier", f"complexity-5 tier: {decisions[-1].tier}"


def test_unmasked_is_constant():
    """Without the mask the released policy is constant — the mask is load-bearing."""
    r = MaskedPAVORouter()
    idxs = set()
    for c in range(1, 6):
        turn = CallTurn(turn_id=c, speaker="rep", text="x", complexity=c, snr_db=42.0, ctx_tokens=200)
        idxs.add(r.route(turn_to_state(turn), apply_mask=False).profile_idx)
    assert len(idxs) == 1, f"unmasked policy should be constant, got {idxs}"


if __name__ == "__main__":
    r = MaskedPAVORouter()
    print(f"[router] loaded, params={r.n_params}")
    print(f"{'complexity':>10} {'profile':>8} {'tier':>12} {'feasible':>9} {'latency':>8} "
          f"{'pavo$':>10} {'frontier$':>10}")
    for c in range(1, 6):
        d = _route_complexity(r, c)
        print(f"{c:>10} {d.profile_idx:>8} {d.tier:>12} {d.feasible_count:>9} "
              f"{d.latency_factor:>7.2f}x {d.pavo_cost_usd:>10.6f} {d.frontier_cost_usd:>10.6f}")
    test_weights_load()
    test_masked_routing_escalates()
    test_unmasked_is_constant()
    print("\nALL TESTS PASSED ✓")

"""Tests for the call loop + cost ledger (SPEC acceptance criteria #4, #5).

    cd orchestrator && python tests/test_call.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backstop.call import run_call
from backstop.ivr_sim import make_scenarios, sample_denial
from backstop.pavo import MaskedPAVORouter


def test_cost_collapse_and_flares():
    router = MaskedPAVORouter()
    denial = sample_denial()
    scenarios = make_scenarios(denial)
    provider = scenarios[0]  # the longest call, with denial-reason turns

    res = run_call(provider, router)

    # AC4: the cost ratio is a real >= 3x collapse
    assert res.ledger.ratio >= 3.0, f"cost ratio too low: {res.ledger.snapshot()}"

    # AC5: the denial-reason turns escalate to frontier; cheap turns dominate
    frontier_on_denial = [
        d for d, t in zip(res.decisions, res.transcript)
        if t.is_denial_reason and d.tier == "frontier"
    ]
    assert frontier_on_denial, "expected >=1 frontier route on a denial-reason turn"

    local = sum(1 for d in res.decisions if d.tier == "local_fast")
    assert local >= len(res.decisions) / 2, (
        f"cheap turns should dominate, got tier_counts={res.ledger.snapshot()['tier_counts']}"
    )


if __name__ == "__main__":
    router = MaskedPAVORouter()
    denial = sample_denial()
    print(f"[denial] {denial.payer} {denial.denial_code} claim {denial.claim_id} ${denial.billed_amount}\n")
    grand = {"pavo": 0.0, "frontier": 0.0}
    for sc in make_scenarios(denial):
        res = run_call(sc, router)
        snap = res.ledger.snapshot()
        grand["pavo"] += snap["pavo_total"]
        grand["frontier"] += snap["frontier_total"]
        flares = sum(1 for d, t in zip(res.decisions, res.transcript) if t.is_denial_reason and d.tier == "frontier")
        print(f"  {sc.label:<38} turns={snap['turns']:>2}  tiers={snap['tier_counts']}  "
              f"ratio={snap['ratio']}x  flares={flares}")
    ratio = grand["frontier"] / grand["pavo"] if grand["pavo"] else 1.0
    print(f"\n  APPEAL TOTAL:  PAVO ${grand['pavo']:.5f}  vs  frontier ${grand['frontier']:.5f}"
          f"   ->  {ratio:.1f}x cheaper")
    test_cost_collapse_and_flares()
    print("\nALL TESTS PASSED ✓")

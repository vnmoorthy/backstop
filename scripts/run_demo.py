#!/usr/bin/env python3
"""Headless Backstop demo — runs the full pipeline and prints the event stream.

    python scripts/run_demo.py

No server, no browser. Proves the pipeline end-to-end on the terminal: intake ->
swarm (PAVO + Moss + cost) -> reconcile -> letter, with the cost collapse and the
Moss rebuttals printed as they fire.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

# make `backstop` importable
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "orchestrator"))

from backstop.integrations import make_clients, sponsor_modes
from backstop.ivr_sim import sample_denial
from backstop.letter import draft_appeal
from backstop.pavo import MaskedPAVORouter
from backstop.reconcile import find_contradiction
from backstop.swarm import Concierge, run_swarm


async def main() -> None:
    router = MaskedPAVORouter()
    clients = make_clients()

    print("=" * 70)
    print("BACKSTOP — denied-claim recovery swarm")
    print("sponsors:", sponsor_modes(clients))
    print("=" * 70)

    def emit(etype: str, payload: dict) -> None:
        if etype == "swarm.spawn":
            ids = [s["id"] for s in payload["specialists"]]
            print(f"\n[swarm] detonated -> {ids}")
        elif etype == "moss.hit":
            print(f"  [moss:{payload['agent_id']}] {payload['magic_words'][:60]}... "
                  f"({int(payload['win_rate']*100)}%)")
        elif etype == "cost.tick" and payload["turns"] % 7 == 0:
            print(f"  [cost] PAVO ${payload['pavo_total']:.4f}  vs  "
                  f"frontier ${payload['frontier_total']:.4f}  ->  {payload['ratio']}x")

    denial = sample_denial()
    spec = Concierge.intake(denial)
    print(f"\n[intake] {denial.payer} {denial.denial_code} claim {denial.claim_id} "
          f"${denial.billed_amount:,.0f}  ->  {spec.required_specialists}")

    out = await run_swarm(spec, router, clients["moss"], emit, gateway=clients["truefoundry"])

    contra = find_contradiction(out["transcripts_by_agent"])
    print(f"\n[reconcile] CLAIM   (t{contra.rep_turn_id}): {contra.claim}")
    print(f"[reconcile] EVIDENCE(t{contra.evidence_turn_id}): {contra.evidence}")

    letter = draft_appeal(spec, contra, out["transcripts_by_agent"], out["rebuttal"])
    print(f"\n[letter] {letter.appeal_id}  ->  {letter.pdf_path or '(md only)'}")

    c = out["cost"]
    print("\n" + "=" * 70)
    print(f"  RESULT: PAVO ${c['pavo_total']:.4f}  vs  frontier ${c['frontier_total']:.4f}"
          f"   ->  {c['ratio']}x cheaper   ({c['tier_counts']})")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())

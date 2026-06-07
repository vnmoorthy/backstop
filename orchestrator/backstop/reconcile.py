"""Reconciler — find the overturning contradiction across the swarm's transcripts.

The pattern is the same across denial codes: the PROVIDER LINE states the denial
reason, and ANOTHER desk (records / prior-auth / billing) confirms the corrective
fact that defeats it. CO-197: "no auth on file" vs "auth A4471 issued". CO-16:
"modifier 25 missing" vs "modifier 25 was on the original 837". CO-50: "no failed
conservative therapy" vs "chart documents failed conservative therapy". We surface
the provider's denial assertion as the claim and the cross-desk fact as the evidence.
"""
from __future__ import annotations

import re

from .models import CallTurn, Contradiction

# language that marks the provider's denial assertion (the "claim")
_DENIAL_LANG = re.compile(r"\b(denied|no\b|not\b|isn't|wasn't|missing|absent|lacks)", re.IGNORECASE)


def _denial_turns(turns: list[CallTurn]) -> list[CallTurn]:
    return [t for t in turns if t.speaker == "rep" and t.is_denial_reason]


def find_contradiction(transcripts_by_agent: dict[str, list[CallTurn]]) -> Contradiction | None:
    provider = transcripts_by_agent.get("provider_line", [])
    provider_denials = _denial_turns(provider)

    # CLAIM: the provider's denial assertion — prefer one using denial language
    claim = None
    for t in provider_denials:
        if _DENIAL_LANG.search(t.text):
            claim = t
            break
    if claim is None and provider_denials:
        claim = provider_denials[0]

    # EVIDENCE: the corrective fact from a DIFFERENT desk (records / prior-auth / billing)
    evidence = None
    for agent, turns in transcripts_by_agent.items():
        if agent == "provider_line":
            continue
        ev = _denial_turns(turns)
        if ev:
            evidence = ev[0]
            break

    # fallback: if no other desk had one (single-desk denial), use the provider's
    # own corrective turn (e.g. CO-197 "...by the rendering NPI I do see A4471")
    if evidence is None and len(provider_denials) >= 2 and claim is not None:
        for t in provider_denials:
            if t.turn_id != claim.turn_id and not _DENIAL_LANG.search(t.text):
                evidence = t
                break

    if claim is None or evidence is None:
        return None

    return Contradiction(
        claim=claim.text,
        evidence=evidence.text,
        rep_turn_id=claim.turn_id,
        evidence_turn_id=evidence.turn_id,
    )


if __name__ == "__main__":
    import asyncio
    from .ivr_sim import Denial, sample_denial
    from .integrations.moss import MossClient
    from .pavo import MaskedPAVORouter
    from .swarm import Concierge, run_swarm

    async def _check(denial):
        spec = Concierge.intake(denial)
        out = await run_swarm(spec, MaskedPAVORouter(), MossClient(), lambda *_: None)
        c = find_contradiction(out["transcripts_by_agent"])
        ok = c is not None
        print(f"  {denial.payer} {denial.denial_code}: "
              + (f"CLAIM(t{c.rep_turn_id})='{c.claim[:40]}...' EVIDENCE(t{c.evidence_turn_id})='{c.evidence[:40]}...'" if ok else "NO CONTRADICTION"))
        assert ok, f"no contradiction for {denial.denial_code}"

    async def _main():
        await _check(sample_denial())  # CO-197
        co16 = Denial("D2", "UnitedHealthcare", "UHC Choice", "TX", "CO-16", ["99214"], 410.0, "2026-02-10", "U900112233", "CLM-22-3344", "1659302341", "1093847551", "")
        co50 = Denial("D3", "Cigna", "Cigna PPO", "TX", "CO-50", ["64483"], 3120.0, "2026-01-22", "C700998877", "CLM-77-9911", "1659302341", "1093847551", "")
        await _check(co16)
        await _check(co50)
        print("\nall denial codes: contradiction found OK")

    asyncio.run(_main())

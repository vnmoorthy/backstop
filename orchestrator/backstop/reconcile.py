"""Reconciler — find the overturning contradiction across the swarm's transcripts.

The CO-197 denial says "no prior authorization on file." The records desk and the
prior-auth desk both confirm an authorization (A4471) DOES exist. That cross-desk
contradiction is what defeats the denial. We surface the provider-line CLAIM and
the strongest EVIDENCE turn (preferring a different desk than the one that denied).
"""
from __future__ import annotations

import re

from .models import CallTurn, Contradiction

_AUTH = re.compile(r"\b(A\d{4,6})\b")
_NO_AUTH = re.compile(r"\bno\b.{0,30}\b(?:prior\s+)?(?:authoriz|auth)\w*", re.IGNORECASE)
_CONFIRM = re.compile(r"(on file|issued|approved|do see|i see|shows)", re.IGNORECASE)


def find_contradiction(transcripts_by_agent: dict[str, list[CallTurn]]) -> Contradiction | None:
    claim: tuple[str, CallTurn] | None = None
    evidence_candidates: list[tuple[str, CallTurn]] = []

    for agent, turns in transcripts_by_agent.items():
        for t in turns:
            if t.speaker != "rep":
                continue
            if claim is None and _NO_AUTH.search(t.text) and "file" in t.text.lower():
                claim = (agent, t)
            if _AUTH.search(t.text) and _CONFIRM.search(t.text):
                evidence_candidates.append((agent, t))

    if claim is None or not evidence_candidates:
        return None

    claim_agent = claim[0]
    # prefer evidence from a DIFFERENT desk than the one that asserted the denial
    cross = [e for e in evidence_candidates if e[0] != claim_agent]
    evidence_agent, evidence_turn = (cross[0] if cross else evidence_candidates[0])

    return Contradiction(
        claim=claim[1].text,
        evidence=evidence_turn.text,
        rep_turn_id=claim[1].turn_id,
        evidence_turn_id=evidence_turn.turn_id,
    )


if __name__ == "__main__":
    import asyncio
    from .ivr_sim import sample_denial
    from .integrations.moss import MossClient
    from .pavo import MaskedPAVORouter
    from .swarm import Concierge, run_swarm

    async def _smoke():
        spec = Concierge.intake(sample_denial())
        out = await run_swarm(spec, MaskedPAVORouter(), MossClient(), lambda *_: None)
        c = find_contradiction(out["transcripts_by_agent"])
        assert c is not None, "expected a contradiction"
        print("CLAIM   (turn %d):" % c.rep_turn_id, c.claim)
        print("EVIDENCE(turn %d):" % c.evidence_turn_id, c.evidence)
        print("\ncontradiction found OK")

    asyncio.run(_smoke())

"""The swarm orchestrator.

A denial detonates into a swarm of specialist call-agents, dispatched by
denial-code gates, run CONCURRENTLY. Each call runs the per-turn PAVO loop
(call.run_call); the swarm intercepts the routing events to (a) keep a single
combined cost ledger for the dashboard's global ticker, and (b) fire Moss
retrieval the instant a rep states the denial reason (the moss.hit cards).

Public API:
  pick_specialists(denial_code) -> list[str]      # the denial-code gates
  Concierge.intake(denial) -> AppealSpec
  async run_swarm(spec, router, moss, emit) -> dict
"""
from __future__ import annotations

import asyncio
import threading
from datetime import date, timedelta

from .call import run_call
from .ivr_sim import make_scenarios
from .models import AppealSpec, Denial


# Denial-code gates: which specialist desks a given denial needs called.
_GATES: dict[str, list[str]] = {
    "CO-197": ["provider_line", "prior_auth_desk", "records_desk"],
    "CO-16": ["provider_line", "billing_office"],
    "CO-50": ["provider_line", "records_desk"],
}


def pick_specialists(denial_code: str) -> list[str]:
    return list(_GATES.get((denial_code or "").upper(), ["provider_line"]))


def _plus_days(iso_or_us: str, days: int) -> str:
    """Best-effort: parse a DOS (ISO or US) and add days; default to today+days."""
    s = (iso_or_us or "").strip()
    try:
        if "/" in s:  # 03/14/2026
            m, d, y = s.split("/")
            base = date(int(y), int(m), int(d))
        else:
            base = date.fromisoformat(s)
    except Exception:
        base = date(2026, 3, 14)
    return (base + timedelta(days=days)).isoformat()


class Concierge:
    """Ingests a denial and produces the appeal spec that seeds the swarm."""

    @staticmethod
    def intake(denial: Denial, parse_confidence: float = 0.86) -> AppealSpec:
        return AppealSpec(
            denial=denial,
            required_specialists=pick_specialists(denial.denial_code),
            sol_deadline=_plus_days(denial.date_of_service, 180),
            parse_confidence=parse_confidence,
        )


class _GlobalCost:
    """Combined cost across every call in the swarm — the dashboard's ticker source."""

    def __init__(self):
        self.pavo = 0.0
        self.frontier = 0.0
        self.turns = 0
        self.tiers = {"local_fast": 0, "mid_reason": 0, "frontier": 0}

    def add(self, payload: dict) -> None:
        self.pavo += payload.get("pavo_cost_usd", 0.0)
        self.frontier += payload.get("frontier_cost_usd", 0.0)
        self.turns += 1
        t = payload.get("tier", "mid_reason")
        self.tiers[t] = self.tiers.get(t, 0) + 1

    def snapshot(self) -> dict:
        ratio = (self.frontier / self.pavo) if self.pavo > 0 else 1.0
        return {
            "pavo_total": round(self.pavo, 6),
            "frontier_total": round(self.frontier, 6),
            "ratio": round(ratio, 2),
            "turns": self.turns,
            "tier_counts": dict(self.tiers),
        }


async def run_swarm(spec: AppealSpec, router, moss, emit, gateway=None) -> dict:
    """Run the specialist swarm concurrently. Returns the aggregate for reconcile + letter."""
    denial = spec.denial
    scenarios = [s for s in make_scenarios(denial) if s.kind in spec.required_specialists]
    if not scenarios:  # safety: always work at least the provider line
        scenarios = make_scenarios(denial)[:1]

    # announce the swarm: cells burst from the PAVO singularity
    emit(
        "swarm.spawn",
        {
            "specialists": [
                {"id": s.agent_id, "kind": s.kind, "label": s.label, "payer": s.payer}
                for s in scenarios
            ]
        },
    )

    gc = _GlobalCost()
    # calls run on worker threads (asyncio.to_thread), so the shared ledger needs
    # a threading lock to keep cost accounting + each snapshot consistent.
    lock = threading.Lock()

    def make_emit(agent_id: str):
        # run_call calls this synchronously from a worker thread; marshal the
        # global-cost update + the global cost.tick + moss.hit back consistently.
        def w(etype: str, payload: dict) -> None:
            if etype == "cost.tick":
                return  # per-call ticks suppressed; the swarm emits the combined one
            if etype == "pavo.route":
                # hold the lock across the whole per-route emission so the cost
                # update, the route event, its cost.tick, the moss hit, and the
                # audit stay atomic and in order across the concurrent calls.
                with lock:
                    gc.add(payload)
                    emit("pavo.route", payload)
                    emit("cost.tick", gc.snapshot())
                    if payload.get("is_denial_reason"):
                        reb = moss.retrieve_rebuttal(denial.denial_code, denial.payer)
                        if reb:
                            emit("moss.hit", {"agent_id": agent_id, **reb.to_dict()})
                    if gateway is not None:
                        gateway.audit({"kind": "route", "agent": agent_id, "tier": payload.get("tier")})
                return
            emit(etype, payload)

        return w

    async def _one(scenario):
        # run_call is sync (the per-turn loop); run it off-thread for real concurrency
        return await asyncio.to_thread(run_call, scenario, router, make_emit(scenario.agent_id))

    results = await asyncio.gather(*[_one(s) for s in scenarios])

    transcripts_by_agent = {r.agent_id: r.transcript for r in results}
    rebuttal = moss.retrieve_rebuttal(denial.denial_code, denial.payer)

    return {
        "transcripts_by_agent": transcripts_by_agent,
        "results": results,
        "cost": gc.snapshot(),
        "rebuttal": rebuttal,
    }


if __name__ == "__main__":
    from .ivr_sim import sample_denial
    from .integrations.moss import MossClient
    from .pavo import MaskedPAVORouter

    def _print_emit(t, p):
        if t in ("swarm.spawn", "moss.hit"):
            print(f"  [{t}] {p}")

    async def _smoke():
        denial = sample_denial()
        spec = Concierge.intake(denial)
        print(f"intake: {denial.payer} {denial.denial_code} -> specialists {spec.required_specialists}, "
              f"deadline {spec.sol_deadline}")
        out = await run_swarm(spec, MaskedPAVORouter(), MossClient(), _print_emit)
        print("\ncombined cost:", out["cost"])
        print("rebuttal:", out["rebuttal"].magic_words[:70] if out["rebuttal"] else None)
        print("agents:", list(out["transcripts_by_agent"].keys()))

    asyncio.run(_smoke())

"""End-to-end functional coverage — every Backstop capability, asserted.

    cd orchestrator && python tests/test_e2e.py
    (or: python -m pytest tests/ -q)
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backstop.integrations import make_clients, sponsor_modes
from backstop.ivr_sim import make_scenarios, sample_denial
from backstop.letter import draft_appeal
from backstop.pavo import MaskedPAVORouter
from backstop.reconcile import find_contradiction
from backstop.swarm import Concierge, pick_specialists, run_swarm

ALL_SPONSORS = {"pavo", "moss", "livekit", "truefoundry", "unsiloed", "aws", "minimax", "qwen"}


def test_pavo_router_loads_and_escalates():
    r = MaskedPAVORouter()
    assert r.n_params == 85041


def test_all_seven_sponsors_present():
    modes = sponsor_modes(make_clients())
    assert ALL_SPONSORS.issubset(set(modes)), f"missing sponsors: {ALL_SPONSORS - set(modes)}"
    assert modes["pavo"] == "real"


def test_moss_returns_the_rebuttal():
    moss = make_clients()["moss"]
    reb = moss.retrieve_rebuttal("CO-197", "Aetna")
    assert reb is not None
    assert "RENDERING NPI" in reb.magic_words.upper()
    assert 0 < reb.win_rate <= 1


def test_unsiloed_parses_denial():
    u = make_clients()["unsiloed"]
    out = u.parse_eob(sample_denial().raw_text)
    assert out["denial_code"] == "CO-197"
    assert out["payer"].lower().startswith("aetna")


def test_denial_code_gates():
    assert pick_specialists("CO-197") == ["provider_line", "prior_auth_desk", "records_desk"]
    assert "provider_line" in pick_specialists("ZZ-999")  # default safety


def test_full_swarm_pipeline():
    async def _run():
        clients = make_clients()
        spec = Concierge.intake(sample_denial())
        assert set(spec.required_specialists) == {"provider_line", "prior_auth_desk", "records_desk"}
        events = []
        out = await run_swarm(spec, MaskedPAVORouter(), clients["moss"],
                              lambda t, p: events.append((t, p)), gateway=clients["truefoundry"])
        etypes = {t for t, _ in events}
        # every pipeline stage fired
        assert {"swarm.spawn", "pavo.route", "moss.hit", "cost.tick"}.issubset(etypes)
        # all three desks produced transcripts
        assert set(out["transcripts_by_agent"]) == {"provider_line", "prior_auth_desk", "records_desk"}
        # cost collapse is real
        assert out["cost"]["ratio"] >= 3.0
        # reconcile + letter
        contra = find_contradiction(out["transcripts_by_agent"])
        assert contra is not None and "A4471" in contra.evidence
        letter = draft_appeal(spec, contra, out["transcripts_by_agent"], out["rebuttal"])
        assert "A4471" in letter.body_md and "RENDERING NPI" in letter.body_md
        return out["cost"]["ratio"]

    ratio = asyncio.run(_run())
    return ratio


if __name__ == "__main__":
    tests = [
        ("pavo router loads + escalates", test_pavo_router_loads_and_escalates),
        ("all 7 sponsors present", test_all_seven_sponsors_present),
        ("moss returns the rebuttal", test_moss_returns_the_rebuttal),
        ("unsiloed parses denial", test_unsiloed_parses_denial),
        ("denial-code gates", test_denial_code_gates),
        ("full swarm pipeline -> reconcile -> letter", test_full_swarm_pipeline),
    ]
    failed = 0
    for name, fn in tests:
        try:
            r = fn()
            extra = f" (ratio {r:.1f}x)" if isinstance(r, float) else ""
            print(f"  PASS  {name}{extra}")
        except Exception as exc:
            failed += 1
            print(f"  FAIL  {name}: {exc!r}")
    print("\n" + ("ALL E2E TESTS PASSED ✓" if not failed else f"{failed} FAILED ✗"))
    sys.exit(1 if failed else 0)

"""Sandbox payer IVR / rep simulator.

By policy (SPEC §2 N1) Backstop never dials a real payer in the demo. These are
deterministic, scripted scenarios that reproduce the shape of a real provider-line
call: IVR navigation + hold (cheap, complexity 1), a rep greeting (complexity 2),
and the 2-3 turns that carry the denial reason (complexity 5 — the "magenta
flares" PAVO escalates to frontier).

The scenarios for a single CO-197 ("no prior auth on file") denial contain a
PLANTED CONTRADICTION the reconciler will find: the provider-line rep says "no
auth on file" (searching by billing NPI), while the records desk and prior-auth
desk both confirm auth A4471 exists (under the rendering NPI). That contradiction
is what overturns the denial.
"""
from __future__ import annotations

from dataclasses import dataclass

from .models import CallTurn, Denial


@dataclass
class Scenario:
    agent_id: str
    kind: str  # SpecialistKind
    payer: str
    label: str
    turns: list[CallTurn]  # inbound IVR/rep utterances the agent must handle


def sample_denial() -> Denial:
    """A synthetic Aetna CO-197 denial (no PHI). The demo's default upload."""
    return Denial(
        denial_id="DEN-2026-0001",
        payer="Aetna",
        plan="Aetna Choice POS II",
        state="TX",
        denial_code="CO-197",
        cpt=["99285"],
        billed_amount=2480.00,
        date_of_service="2026-03-14",
        member_id="W812340099",
        claim_id="CLM-55-7741",
        rendering_npi="1659302341",
        billing_npi="1093847551",
        raw_text=(
            "AETNA EXPLANATION OF BENEFITS\n"
            "Claim CLM-55-7741  DOS 03/14/2026  CPT 99285  Billed $2,480.00\n"
            "DENIAL CO-197: Precertification/authorization absent.\n"
            "Member W812340099  Plan: Aetna Choice POS II (TX)"
        ),
    )


def make_scenarios(denial: Denial) -> list[Scenario]:
    """Three sandbox IVRs for a CO-197 denial, with the planted auth contradiction."""
    p = denial.payer

    provider_line = Scenario(
        agent_id="provider_line",
        kind="provider_line",
        payer=p,
        label=f"{p} provider services",
        turns=[
            CallTurn(0, "ivr", f"Thank you for calling {p} provider services. For claims, press 3.", complexity=1, snr_db=36, ctx_tokens=40),
            CallTurn(1, "ivr", "Please say or enter the provider tax ID.", complexity=1, snr_db=30, ctx_tokens=30),
            CallTurn(2, "ivr", "Please hold. Your estimated wait is 14 minutes.", complexity=1, snr_db=20, ctx_tokens=16),
            # The 14-minute hold: a real voice agent runs one cheap inference per
            # check, listening for a human to pick up. PAVO keeps all of these on
            # the near-free local tier. This is where the cost collapse lives.
            CallTurn(3, "ivr", "...hold music...", complexity=1, snr_db=14, ctx_tokens=8),
            CallTurn(4, "ivr", "...still holding...", complexity=1, snr_db=13, ctx_tokens=6),
            CallTurn(5, "ivr", "...still holding...", complexity=1, snr_db=13, ctx_tokens=6),
            CallTurn(6, "ivr", "Your call is important to us. Please continue to hold.", complexity=1, snr_db=15, ctx_tokens=14),
            CallTurn(7, "ivr", "...still holding...", complexity=1, snr_db=12, ctx_tokens=6),
            CallTurn(8, "ivr", "...still holding...", complexity=1, snr_db=12, ctx_tokens=6),
            CallTurn(9, "rep", "Provider services, this is Dana. Claim number?", complexity=2, snr_db=29, ctx_tokens=60),
            CallTurn(10, "rep", f"That claim was denied {denial.denial_code} — there is no prior authorization on file.", complexity=5, snr_db=27, ctx_tokens=180, is_denial_reason=True),
            CallTurn(11, "rep", "I'm checking under the billing NPI and I don't see any auth.", complexity=4, snr_db=26, ctx_tokens=130),
            CallTurn(12, "rep", "Hold on — searching by the rendering NPI, I do see authorization A4471. I'll send it back to reprocess. Reference 7741.", complexity=5, snr_db=25, ctx_tokens=170, is_denial_reason=True),
        ],
    )

    prior_auth_desk = Scenario(
        agent_id="prior_auth_desk",
        kind="prior_auth_desk",
        payer=p,
        label=f"{p} prior-authorization desk",
        turns=[
            CallTurn(0, "ivr", "Prior authorization. Press 1 to verify an existing auth.", complexity=1, snr_db=34, ctx_tokens=30),
            CallTurn(1, "ivr", "Please hold for the next specialist.", complexity=1, snr_db=19, ctx_tokens=12),
            CallTurn(2, "rep", "Auth desk, member ID please.", complexity=2, snr_db=31, ctx_tokens=50),
            CallTurn(3, "rep", "Yes — authorization A4471 was issued for that date of service, tied to the rendering provider.", complexity=5, snr_db=28, ctx_tokens=150, is_denial_reason=True),
        ],
    )

    records_desk = Scenario(
        agent_id="records_desk",
        kind="records_desk",
        payer=p,
        label="Referring-physician records desk",
        turns=[
            CallTurn(0, "ivr", "Medical records. Press 2 for authorization records.", complexity=1, snr_db=33, ctx_tokens=28),
            CallTurn(1, "ivr", "Please hold.", complexity=1, snr_db=17, ctx_tokens=8),
            CallTurn(2, "rep", "Records, go ahead.", complexity=2, snr_db=30, ctx_tokens=40),
            CallTurn(3, "rep", "The chart shows pre-auth A4471 on file, approved before the visit.", complexity=5, snr_db=29, ctx_tokens=140, is_denial_reason=True),
        ],
    )

    return [provider_line, prior_auth_desk, records_desk]

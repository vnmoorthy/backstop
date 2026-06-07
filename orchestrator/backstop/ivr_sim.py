"""Sandbox payer IVR / rep simulator.

By policy (SPEC §2 N1) Backstop never dials a real payer in the demo. These are
deterministic, scripted scenarios that reproduce the shape of a real provider-line
call: IVR navigation + hold (cheap, complexity 1), a rep greeting (complexity 2),
and the 2-3 turns that carry the denial reason (complexity 5 — the "magenta
flares" PAVO escalates to frontier).

Each scenario set contains a PLANTED CONTRADICTION the reconciler finds: the
provider line states the denial reason, while another desk confirms the corrective
fact that overturns it. Dispatched by denial code so you can run several denial
types live (CO-197 auth-lookup defect, CO-16 missing-info, CO-50 medical-necessity).
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
    """A synthetic Aetna CO-197 denial (no PHI). The demo's default."""
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


def _hold_turns(payer: str, start: int) -> list[CallTurn]:
    """A realistic 14-minute hold: many cheap inference steps PAVO keeps local."""
    return [
        CallTurn(start + 0, "ivr", f"Thank you for calling {payer} provider services. For claims, press 3.", complexity=1, snr_db=36, ctx_tokens=40),
        CallTurn(start + 1, "ivr", "Please say or enter the provider tax ID.", complexity=1, snr_db=30, ctx_tokens=30),
        CallTurn(start + 2, "ivr", "Please hold. Your estimated wait is 14 minutes.", complexity=1, snr_db=20, ctx_tokens=16),
        CallTurn(start + 3, "ivr", "...hold music...", complexity=1, snr_db=14, ctx_tokens=8),
        CallTurn(start + 4, "ivr", "...still holding...", complexity=1, snr_db=13, ctx_tokens=6),
        CallTurn(start + 5, "ivr", "...still holding...", complexity=1, snr_db=13, ctx_tokens=6),
        CallTurn(start + 6, "ivr", "Your call is important to us. Please continue to hold.", complexity=1, snr_db=15, ctx_tokens=14),
        CallTurn(start + 7, "ivr", "...still holding...", complexity=1, snr_db=12, ctx_tokens=6),
        CallTurn(start + 8, "ivr", "...still holding...", complexity=1, snr_db=12, ctx_tokens=6),
    ]


def _co197_scenarios(denial: Denial) -> list[Scenario]:
    """CO-197 — auth-on-file lookup defect (billing NPI vs rendering NPI)."""
    p = denial.payer
    provider_line = Scenario(
        "provider_line", "provider_line", p, f"{p} provider services",
        _hold_turns(p, 0) + [
            CallTurn(9, "rep", "Provider services, this is Dana. Claim number?", complexity=2, snr_db=29, ctx_tokens=60),
            CallTurn(10, "rep", f"That claim was denied {denial.denial_code} — there is no prior authorization on file.", complexity=5, snr_db=27, ctx_tokens=180, is_denial_reason=True),
            CallTurn(11, "rep", "I'm checking under the billing NPI and I don't see any auth.", complexity=4, snr_db=26, ctx_tokens=130),
            CallTurn(12, "rep", "Hold on — searching by the rendering NPI, I do see authorization A4471. I'll send it back to reprocess. Reference 7741.", complexity=5, snr_db=25, ctx_tokens=170, is_denial_reason=True),
        ],
    )
    prior_auth_desk = Scenario(
        "prior_auth_desk", "prior_auth_desk", p, f"{p} prior-authorization desk",
        [
            CallTurn(0, "ivr", "Prior authorization. Press 1 to verify an existing auth.", complexity=1, snr_db=34, ctx_tokens=30),
            CallTurn(1, "ivr", "Please hold for the next specialist.", complexity=1, snr_db=19, ctx_tokens=12),
            CallTurn(2, "rep", "Auth desk, member ID please.", complexity=2, snr_db=31, ctx_tokens=50),
            CallTurn(3, "rep", "Yes — authorization A4471 was issued for that date of service, tied to the rendering provider.", complexity=5, snr_db=28, ctx_tokens=150, is_denial_reason=True),
        ],
    )
    records_desk = Scenario(
        "records_desk", "records_desk", p, "Referring-physician records desk",
        [
            CallTurn(0, "ivr", "Medical records. Press 2 for authorization records.", complexity=1, snr_db=33, ctx_tokens=28),
            CallTurn(1, "ivr", "Please hold.", complexity=1, snr_db=17, ctx_tokens=8),
            CallTurn(2, "rep", "Records, go ahead.", complexity=2, snr_db=30, ctx_tokens=40),
            CallTurn(3, "rep", "The chart shows pre-auth A4471 on file, approved before the visit.", complexity=5, snr_db=29, ctx_tokens=140, is_denial_reason=True),
        ],
    )
    return [provider_line, prior_auth_desk, records_desk]


def _co16_scenarios(denial: Denial) -> list[Scenario]:
    """CO-16 — claim/service lacks information (a specific modifier was dropped)."""
    p = denial.payer
    provider_line = Scenario(
        "provider_line", "provider_line", p, f"{p} provider services",
        _hold_turns(p, 0) + [
            CallTurn(9, "rep", "Provider services. Claim number?", complexity=2, snr_db=29, ctx_tokens=60),
            CallTurn(10, "rep", f"It denied {denial.denial_code} — missing information. Modifier 25 isn't on the E/M line.", complexity=5, snr_db=27, ctx_tokens=170, is_denial_reason=True),
            CallTurn(11, "rep", "On my side the line came in without the modifier.", complexity=4, snr_db=26, ctx_tokens=110),
        ],
    )
    billing_office = Scenario(
        "billing_office", "billing_office", p, "Hospital billing office",
        [
            CallTurn(0, "ivr", "Billing. Press 4 for claim corrections.", complexity=1, snr_db=33, ctx_tokens=26),
            CallTurn(1, "ivr", "Please hold.", complexity=1, snr_db=17, ctx_tokens=8),
            CallTurn(2, "rep", "Billing, go ahead.", complexity=2, snr_db=30, ctx_tokens=40),
            CallTurn(3, "rep", "Our submitted 837 shows modifier 25 WAS appended to the E/M line — it was dropped in their intake. Here's the original line.", complexity=5, snr_db=28, ctx_tokens=150, is_denial_reason=True),
        ],
    )
    return [provider_line, billing_office]


def _co50_scenarios(denial: Denial) -> list[Scenario]:
    """CO-50 — not deemed medically necessary (LCD criterion is actually met)."""
    p = denial.payer
    provider_line = Scenario(
        "provider_line", "provider_line", p, f"{p} provider services",
        _hold_turns(p, 0) + [
            CallTurn(9, "rep", "Provider services. Claim number?", complexity=2, snr_db=29, ctx_tokens=60),
            CallTurn(10, "rep", f"Denied {denial.denial_code} — not medically necessary; the reviewer didn't see failed conservative therapy.", complexity=5, snr_db=27, ctx_tokens=180, is_denial_reason=True),
            CallTurn(11, "rep", "There's no note of prior treatment in the record I'm looking at.", complexity=4, snr_db=26, ctx_tokens=120),
        ],
    )
    records_desk = Scenario(
        "records_desk", "records_desk", p, "Referring-physician records desk",
        [
            CallTurn(0, "ivr", "Medical records. Press 2 for clinical notes.", complexity=1, snr_db=33, ctx_tokens=28),
            CallTurn(1, "ivr", "Please hold.", complexity=1, snr_db=17, ctx_tokens=8),
            CallTurn(2, "rep", "Records.", complexity=2, snr_db=30, ctx_tokens=36),
            CallTurn(3, "rep", "The chart documents six weeks of failed conservative therapy before the procedure — that meets the LCD criterion for medical necessity.", complexity=5, snr_db=29, ctx_tokens=160, is_denial_reason=True),
        ],
    )
    return [provider_line, records_desk]


def make_scenarios(denial: Denial) -> list[Scenario]:
    """Return the sandbox call scenarios for a denial, dispatched by denial code."""
    code = (denial.denial_code or "").upper()
    if code == "CO-16":
        return _co16_scenarios(denial)
    if code == "CO-50":
        return _co50_scenarios(denial)
    return _co197_scenarios(denial)  # CO-197 + default

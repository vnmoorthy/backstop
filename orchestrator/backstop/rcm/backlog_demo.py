"""End-to-end closed loop on a real X12 835 backlog.

Run:  python3 -m backstop.rcm.backlog_demo

Parses a real-format 835 remittance into a recoverable worklist, drafts a real
(MiniMax) appeal argument for the top item, applies the follow-up remittance as
the outcome, and prints recovered dollars + the contingency invoice. This is the
billable loop, demonstrated on real remittance format.
"""
from __future__ import annotations

import os
from datetime import date
from pathlib import Path

from backstop.rcm.recovery import reconcile, triage
from backstop.rcm.x12_835 import parse_835

_REPO_ROOT = Path(__file__).resolve().parents[3]
_BACKLOG = _REPO_ROOT / "data" / "backlog"


def _real_appeal_line(carc: str, amount: float, claim_id: str) -> tuple[str, str]:
    """Return ``(mode, text)`` — a real MiniMax appeal line, else a grounded local one."""
    key = os.getenv("MINIMAX_API_KEY")
    if key:
        try:
            import httpx

            base = os.getenv("MINIMAX_BASE_URL", "https://api.minimax.io/v1")
            r = httpx.post(
                base + "/text/chatcompletion_v2",
                headers={"Authorization": f"Bearer {key}"},
                json={
                    "model": os.getenv("MINIMAX_MODEL", "MiniMax-Text-01"),
                    "messages": [
                        {
                            "role": "user",
                            "content": (
                                f"Claim {claim_id} was denied CARC {carc} for "
                                f"${amount:,.2f}. In ONE sentence, state the precise "
                                "appeal argument to overturn it."
                            ),
                        }
                    ],
                },
                timeout=30.0,
            )
            txt = r.json().get("choices", [{}])[0].get("message", {}).get("content", "")
            if txt:
                return "real", txt.strip()
        except Exception:  # noqa: S110 - demo: degrade to a local line on any hiccup
            pass
    return (
        "sim",
        f"Appeal {claim_id}: the CARC {carc} adjustment is unsupported; the "
        "documentation on file satisfies the plan requirement — reprocess and "
        f"pay ${amount:,.2f}.",
    )


def main() -> None:
    """Run the closed loop on the sample backlog and print the invoice."""
    intake = parse_835((_BACKLOG / "intake_835.txt").read_text())
    followup = parse_835((_BACKLOG / "remit_followup_835.txt").read_text())
    today = date(2026, 6, 1)

    line = "=" * 66
    print(line)
    print(" BACKSTOP — written-off backlog  ->  recovered $  ->  invoice")
    print(line)
    print(f" Payer: {intake.payer}      Claims in remittance: {len(intake.claims)}")
    den = intake.denials
    written_off = sum(c.denied_amount for c in den)
    print(f" Denied claims: {len(den)}      Written-off $: ${written_off:,.2f}")
    print()

    work = triage(intake, today)
    print(" TRIAGE WORKLIST  (recoverable $ x timely-filing urgency)")
    for i, it in enumerate(work, 1):
        print(
            f"   {i}. {it.claim.claim_id}  CARC {it.carc:<4}  "
            f"${it.recoverable:>9,.2f}  · {it.days_to_deadline}d to deadline"
            f"  · score {it.priority:,.0f}"
        )
    appealed = [it.claim.claim_id for it in work]
    print()

    if work:
        top = work[0]
        mode, text = _real_appeal_line(top.carc, top.recoverable, top.claim.claim_id)
        print(f" REAL APPEAL ({mode}) for {top.claim.claim_id}:")
        print(f"   {text[:220]}")
        print()

    res = reconcile(intake, followup, appealed, contingency_rate=0.27)
    print(" OUTCOME  (after the follow-up remittance)")
    print(f"   Recoverable (appealed):   ${res.recoverable_total:>10,.2f}")
    print(f"   Appeals filed:            {res.appealed_count:>11}")
    print(
        f"   Overturned (won):         {res.won_count:>11}"
        f"   ({res.win_rate * 100:.0f}% win rate)"
    )
    print(f"   RECOVERED DOLLARS:        ${res.recovered_total:>10,.2f}")
    print(line)
    print(
        f"   CONTINGENCY INVOICE ({res.contingency_rate * 100:.0f}%):  "
        f"${res.invoice_amount:>10,.2f}"
    )
    print(line)


if __name__ == "__main__":
    main()

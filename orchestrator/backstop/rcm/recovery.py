"""Recovery ledger + contingency billing — the loop that makes Backstop billable.

Three jobs:
  1. triage()    — rank denials by recoverable-$ x timely-filing urgency.
  2. reconcile() — given the intake remittance (denials) and a follow-up
                   remittance (outcomes after appeals), compute recovered dollars
                   and the win rate.
  3. invoice math — recovered dollars x the contingency rate (e.g. 27%).

This is what turns "we drafted an appeal" into "we recovered $X and here is the
invoice for our 27%." Pure, deterministic, auditable.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Dict, List

from backstop.rcm.x12_835 import ClaimPayment, Remittance

# CARCs that are realistically appealable (a curated subset; production systems
# maintain a far larger payer-specific table). PR-group adjustments are patient
# responsibility and are excluded by the parser's denied_amount already.
APPEALABLE_CARCS = {
    "16",   # missing/incomplete information
    "18",   # duplicate (often reversible)
    "45",   # charge exceeds fee schedule (contractual — sometimes appealable)
    "50",   # not medically necessary
    "96",   # non-covered (sometimes appealable with documentation)
    "109",  # not covered by this payer/contractor
    "197",  # precert/authorization absent
    "204",  # not covered under the plan
    "B7",   # provider not certified/eligible for this DOS
}

# Default timely-filing window used to compute urgency when none is known.
_DEFAULT_FILING_DAYS = 180


@dataclass
class TriageItem:
    """One denial on the worklist, scored for prioritization."""

    claim: ClaimPayment
    recoverable: float
    carc: str
    days_to_deadline: int
    priority: float  # recoverable $ weighted by deadline urgency


def _days_to_deadline(dos: str, today: date, filing_days: int = _DEFAULT_FILING_DAYS) -> int:
    """Days remaining until the timely-filing deadline (DOS + filing window)."""
    try:
        d = datetime.strptime(dos, "%Y%m%d").date()
    except (TypeError, ValueError):
        return filing_days
    return (d + timedelta(days=filing_days) - today).days


def triage(
    rem: Remittance, today: date, filing_days: int = _DEFAULT_FILING_DAYS
) -> List[TriageItem]:
    """Rank appealable denials by recoverable $ with a deadline-urgency multiplier."""
    items: List[TriageItem] = []
    for c in rem.denials:
        if c.primary_carc not in APPEALABLE_CARCS:
            continue
        recoverable = c.denied_amount or round(c.billed - c.paid, 2)
        dtd = _days_to_deadline(c.dos, today, filing_days)
        # urgency: 1.0 with a full window, rising as the deadline approaches; a
        # claim already past the window gets a small floor (still worth a look).
        urgency = 1.0 + max(0.0, (filing_days - max(dtd, 0)) / filing_days)
        items.append(
            TriageItem(
                claim=c,
                recoverable=recoverable,
                carc=c.primary_carc,
                days_to_deadline=dtd,
                priority=round(recoverable * urgency, 2),
            )
        )
    items.sort(key=lambda x: x.priority, reverse=True)
    return items


@dataclass
class RecoveryResult:
    """The closed-loop outcome + the contingency invoice."""

    backlog_denied_total: float
    recoverable_total: float
    appealed_count: int
    won_count: int
    recovered_total: float
    win_rate: float
    contingency_rate: float
    invoice_amount: float


def reconcile(
    intake: Remittance,
    followup: Remittance,
    appealed_ids: List[str],
    contingency_rate: float = 0.27,
) -> RecoveryResult:
    """Compare the follow-up remittance to intake to compute recovered dollars.

    A claim is 'won' when its paid amount in the follow-up remittance exceeds its
    paid amount at intake. Recovered dollars = the sum of those positive deltas.
    The invoice is recovered dollars x the contingency rate.
    """
    paid_before: Dict[str, float] = {c.claim_id: c.paid for c in intake.claims}
    paid_after: Dict[str, float] = {c.claim_id: c.paid for c in followup.claims}
    appealed = set(appealed_ids)

    recoverable = 0.0
    for c in intake.claims:
        if c.claim_id in appealed and c.is_denied:
            recoverable += c.denied_amount or (c.billed - c.paid)

    won = 0
    recovered = 0.0
    for cid in appealed:
        delta = paid_after.get(cid, paid_before.get(cid, 0.0)) - paid_before.get(cid, 0.0)
        if delta > 0.0:
            won += 1
            recovered += delta

    n = len(appealed)
    return RecoveryResult(
        backlog_denied_total=round(sum(c.denied_amount for c in intake.denials), 2),
        recoverable_total=round(recoverable, 2),
        appealed_count=n,
        won_count=won,
        recovered_total=round(recovered, 2),
        win_rate=round(won / n, 3) if n else 0.0,
        contingency_rate=contingency_rate,
        invoice_amount=round(recovered * contingency_rate, 2),
    )

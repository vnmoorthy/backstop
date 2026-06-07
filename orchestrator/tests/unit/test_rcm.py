"""Tests for the RCM closed loop: 835 parsing, triage, recovery + invoice."""
from __future__ import annotations

from datetime import date
from pathlib import Path

from backstop.rcm.recovery import APPEALABLE_CARCS, reconcile, triage
from backstop.rcm.x12_835 import parse_835

_BACKLOG = Path(__file__).resolve().parents[3] / "data" / "backlog"


def _intake():
    return parse_835((_BACKLOG / "intake_835.txt").read_text())


def _followup():
    return parse_835((_BACKLOG / "remit_followup_835.txt").read_text())


def test_parser_extracts_denials_amounts_and_carcs():
    rem = _intake()
    assert rem.payer == "AETNA"
    assert len(rem.claims) == 7
    den = rem.denials
    # 5 denied (197, 50, 16, 45, B7); 2 paid (PR patient-resp only)
    assert len(den) == 5
    by_id = {c.claim_id: c for c in rem.claims}
    assert by_id["CLM-55-7741"].primary_carc == "197"
    assert by_id["CLM-55-7741"].denied_amount == 2480.00
    assert by_id["CLM-55-7741"].is_denied is True
    # a paid claim with only patient-responsibility is NOT a denial to appeal
    assert by_id["CLM-55-7744"].is_denied is False
    assert by_id["CLM-55-7744"].paid == 720.00


def test_triage_ranks_by_recoverable_value():
    work = triage(_intake(), date(2026, 6, 1))
    # all appealable CARCs present
    assert {it.carc for it in work} <= APPEALABLE_CARCS
    # highest recoverable first (CLM-...-7745 at $3,120)
    assert work[0].claim.claim_id == "CLM-55-7745"
    assert work[0].recoverable == 3120.00
    # sorted descending by priority
    assert all(work[i].priority >= work[i + 1].priority for i in range(len(work) - 1))


def test_reconcile_computes_recovered_dollars_and_invoice():
    intake, followup = _intake(), _followup()
    appealed = [c.claim_id for c in intake.denials]  # appeal all 5
    res = reconcile(intake, followup, appealed, contingency_rate=0.27)
    # 7741 (2480) + 7742 (1640) + 7745 (3120) now paid; 7743 + 7746 still denied
    assert res.won_count == 3
    assert res.recovered_total == 7240.00
    assert res.win_rate == round(3 / 5, 3)
    assert res.invoice_amount == round(7240.00 * 0.27, 2)  # 1954.80


def test_no_recovery_when_followup_unchanged():
    intake = _intake()
    res = reconcile(intake, intake, [c.claim_id for c in intake.denials])
    assert res.recovered_total == 0.0
    assert res.invoice_amount == 0.0

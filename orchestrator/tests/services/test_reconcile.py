"""Tests for :class:`ReconcileService` cross-desk contradiction detection.

The canonical case: three desks report incompatible denial reasons for one
claim — CO-197 (prior-auth absent), CO-50 (non-covered), CO-16 (missing info).
The payer cannot consistently hold all three, so a contradiction is reported.
"""

from __future__ import annotations

from backstop.domain.enums import SpecialistKind
from backstop.services.reconcile_service import DeskFinding, ReconcileService


def _svc() -> ReconcileService:
    """Build the (stateless) reconcile service."""
    return ReconcileService()


def test_finds_co197_co50_co16_contradiction() -> None:
    """Prior-auth vs non-covered vs missing-info across desks contradicts."""
    findings = [
        DeskFinding(SpecialistKind.PRIOR_AUTH_DESK, "CO-197"),
        DeskFinding(SpecialistKind.PROVIDER_LINE, "CO-50"),
        DeskFinding(SpecialistKind.BILLING_OFFICE, "CO-16"),
    ]
    result = _svc().find_contradiction(findings)

    assert result.found is True
    assert result.groups == ("missing_info", "non_covered", "prior_auth")
    assert len(result.findings) == 3
    assert "cross-desk contradiction" in result.summary


def test_two_conflicting_desks_is_enough() -> None:
    """Two findings in different groups already contradict."""
    findings = [
        DeskFinding(SpecialistKind.PRIOR_AUTH_DESK, "197"),
        DeskFinding(SpecialistKind.PROVIDER_LINE, "50"),
    ]
    result = _svc().find_contradiction(findings)

    assert result.found is True
    assert result.groups == ("non_covered", "prior_auth")


def test_codes_normalize_with_or_without_prefix() -> None:
    """``CO-197`` and ``197`` resolve to the same group."""
    findings = [
        DeskFinding(SpecialistKind.PRIOR_AUTH_DESK, "CO-197"),
        DeskFinding(SpecialistKind.RECORDS_DESK, "197"),
    ]
    # Both prior_auth → no contradiction (single group).
    assert _svc().find_contradiction(findings).found is False


def test_agreeing_desks_no_contradiction() -> None:
    """Desks that all report the same assertion do not contradict."""
    findings = [
        DeskFinding(SpecialistKind.PROVIDER_LINE, "CO-50"),
        DeskFinding(SpecialistKind.BILLING_OFFICE, "CO-96"),
    ]
    assert _svc().find_contradiction(findings).found is False


def test_empty_and_unknown_codes_no_contradiction() -> None:
    """No findings, or only unrecognized codes, yields no contradiction."""
    assert _svc().find_contradiction([]).found is False
    unknown = [DeskFinding(SpecialistKind.PROVIDER_LINE, "ZZ-999")]
    assert _svc().find_contradiction(unknown).found is False


def test_summary_names_desks_and_codes() -> None:
    """The summary is explainable: it names the conflicting codes and desks."""
    findings = [
        DeskFinding(SpecialistKind.PRIOR_AUTH_DESK, "CO-197"),
        DeskFinding(SpecialistKind.PROVIDER_LINE, "CO-50"),
    ]
    summary = _svc().find_contradiction(findings).summary
    assert "197" in summary and "50" in summary
    assert "PRIOR_AUTH_DESK" in summary and "PROVIDER_LINE" in summary

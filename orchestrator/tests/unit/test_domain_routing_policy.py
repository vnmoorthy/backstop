"""Unit tests for :mod:`backstop.domain.routing_policy`.

``routing_policy.decide(denial, table)`` is a pure mapping from a denial to a
:class:`RouteDecision`. It starts from the CARC's ``default_route`` in the
loaded :class:`CarcTable` and then applies deterministic RARC overrides: when
the base route is ``APPEAL``, a missing-information remark steers it to
``RESUBMIT`` and a medical-necessity remark steers it to ``PEER_TO_PEER``. An
unknown CARC falls back to :data:`FALLBACK_ROUTE`.

These tests drive the real table (loaded from ``data/carc_rarc.json``) so the
mapping is exercised end-to-end against the shipped data deliverable.
"""

from __future__ import annotations

import datetime as _dt
from typing import Optional

import pytest

from backstop.domain.carc_table import CarcTable, load_carc_table
from backstop.domain.enums import RouteDecision
from backstop.domain.models import Denial, Payer
from backstop.domain.money import Money
from backstop.domain.routing_policy import FALLBACK_ROUTE, decide

_PAYER = Payer(payer_id="payer-1", name="Acme Health")


def _denial(carc: str, rarc: Optional[str] = None) -> Denial:
    """Build a real :class:`Denial` carrying the given CARC and optional RARC."""
    return Denial(
        denial_id="denial-1",
        payer=_PAYER,
        plan="PPO",
        member_id_hash="hash-member",
        claim_number_hash="hash-claim",
        denial_code=carc,
        rarc=rarc,
        cpt=("99213",),
        billed=Money(cents=50_000),
        dos=_dt.date(2026, 6, 1),
    )


@pytest.fixture(scope="module")
def table() -> CarcTable:
    """Load the shipped CARC/RARC table once for the module."""
    return load_carc_table()


# --------------------------------------------------------------------------- #
# Canonical CARC -> RouteDecision mappings (from the table's default_route).
# --------------------------------------------------------------------------- #
def test_missing_authorization_routes_to_appeal(table: CarcTable) -> None:
    """CARC 197 (precert/authorization absent) -> APPEAL."""
    assert decide(_denial("197"), table) is RouteDecision.APPEAL


def test_medical_necessity_default_route_is_appeal(table: CarcTable) -> None:
    """CARC 50 (medical necessity) defaults to APPEAL without a RARC."""
    assert decide(_denial("50"), table) is RouteDecision.APPEAL


def test_coding_or_data_error_routes_to_resubmit(table: CarcTable) -> None:
    """A correctable missing-information/coding error -> RESUBMIT."""
    assert decide(_denial("16"), table) is RouteDecision.RESUBMIT


def test_patient_responsibility_routes_to_write_off(table: CarcTable) -> None:
    """A patient-responsibility balance (CARC 1, deductible) -> WRITE_OFF."""
    assert decide(_denial("1"), table) is RouteDecision.WRITE_OFF


# --------------------------------------------------------------------------- #
# RARC overrides (only applied when the base route is APPEAL).
# --------------------------------------------------------------------------- #
def test_medical_necessity_rarc_escalates_to_peer_to_peer(table: CarcTable) -> None:
    """An APPEAL base + medical-necessity RARC -> PEER_TO_PEER."""
    assert decide(_denial("50", rarc="N115"), table) is RouteDecision.PEER_TO_PEER


def test_missing_info_rarc_steers_appeal_to_resubmit(table: CarcTable) -> None:
    """An APPEAL base + missing-information RARC -> RESUBMIT."""
    assert decide(_denial("197", rarc="N01"), table) is RouteDecision.RESUBMIT


def test_rarc_override_does_not_apply_to_non_appeal_base(table: CarcTable) -> None:
    """A RARC override is ignored when the base route is not APPEAL."""
    # CARC 16 already routes to RESUBMIT; a peer-to-peer RARC must not flip it.
    assert decide(_denial("16", rarc="N115"), table) is RouteDecision.RESUBMIT


# --------------------------------------------------------------------------- #
# Coverage of the full RouteDecision codomain + the fallback.
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("carc", "rarc", "expected"),
    [
        ("197", None, RouteDecision.APPEAL),
        ("50", "N115", RouteDecision.PEER_TO_PEER),
        ("16", None, RouteDecision.RESUBMIT),
        ("1", None, RouteDecision.WRITE_OFF),
    ],
)
def test_policy_table_is_exhaustive(
    table: CarcTable, carc: str, rarc: Optional[str], expected: RouteDecision
) -> None:
    """Each representative denial resolves to its documented route."""
    assert decide(_denial(carc, rarc), table) is expected


def test_every_route_decision_is_reachable(table: CarcTable) -> None:
    """The representative inputs cover all four RouteDecision members."""
    produced = {
        decide(_denial("197"), table),
        decide(_denial("50", rarc="N115"), table),
        decide(_denial("16"), table),
        decide(_denial("1"), table),
    }
    assert produced == set(RouteDecision)


def test_unknown_carc_uses_fallback_route(table: CarcTable) -> None:
    """An unknown CARC routes to the conservative fallback (RESUBMIT)."""
    assert decide(_denial("ZZ-UNKNOWN"), table) is FALLBACK_ROUTE
    assert FALLBACK_ROUTE is RouteDecision.RESUBMIT


# --------------------------------------------------------------------------- #
# Determinism + purity.
# --------------------------------------------------------------------------- #
def test_decide_is_deterministic(table: CarcTable) -> None:
    """The same denial always routes the same way."""
    denial = _denial("197")
    assert decide(denial, table) is decide(denial, table)


def test_decide_does_not_mutate_denial(table: CarcTable) -> None:
    """Routing reads the denial without mutating it."""
    denial = _denial("50", rarc="N115")
    decide(denial, table)
    assert denial.denial_code == "50"
    assert denial.rarc == "N115"

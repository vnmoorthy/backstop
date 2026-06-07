"""A real X12 835 (Electronic Remittance Advice) parser.

The 835 is the file hospitals actually receive from payers: it says, per claim,
what was billed, what was paid, and — crucially — the CARC (Claim Adjustment
Reason Code) for every dollar that was *not* paid. Parsing it is the un-fakeable
on-ramp that turns a written-off remittance into a recoverable worklist.

Scope: the segments that matter for denial recovery (CLP/CAS/NM1/DTM under
N1*PR). Deterministic, dependency-free, offline. Not a full HIPAA 835 validator,
but it parses real-format remittances correctly.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

# CLP02 — claim status codes (X12 835)
_STATUS = {
    "1": "processed_as_primary",
    "2": "processed_as_secondary",
    "3": "processed_as_tertiary",
    "4": "denied",
    "19": "processed_forwarded",
    "22": "reversal_of_prior",
    "23": "not_our_claim",
}

# Adjustment group codes whose dollars the provider can pursue (vs PR = patient
# responsibility, which is not a payer denial to appeal).
_PROVIDER_GROUPS = {"CO", "PI", "OA", "CR"}


@dataclass(frozen=True)
class Adjustment:
    """One CAS adjustment: a group code, a CARC, and the dollars adjusted."""

    group: str  # CO (contractual), PR (patient), OA (other), PI (payer-initiated), CR
    carc: str   # reason code, e.g. "197"
    amount: float


@dataclass
class ClaimPayment:
    """A single claim's remittance line (CLP) plus its adjustments and party info."""

    claim_id: str
    status_code: str
    billed: float
    paid: float
    patient_resp: float
    payer: str = ""
    member_id: str = ""
    dos: str = ""
    adjustments: List[Adjustment] = field(default_factory=list)

    @property
    def status(self) -> str:
        """Human-readable claim status from the CLP02 code."""
        return _STATUS.get(self.status_code, "unknown")

    @property
    def denied_amount(self) -> float:
        """Dollars the payer adjusted off under a provider-pursuable group code."""
        return round(
            sum(a.amount for a in self.adjustments if a.group in _PROVIDER_GROUPS), 2
        )

    @property
    def is_denied(self) -> bool:
        """True if the claim was denied outright or zero-paid with adjustments."""
        if self.status_code == "4":
            return True
        return self.paid == 0.0 and self.billed > 0.0 and self.denied_amount > 0.0

    @property
    def primary_carc(self) -> str:
        """The first provider-pursuable CARC on the claim (or empty)."""
        for a in self.adjustments:
            if a.group in _PROVIDER_GROUPS:
                return a.carc
        return ""


@dataclass
class Remittance:
    """A parsed 835: the payer and every claim line in it."""

    payer: str
    claims: List[ClaimPayment]

    @property
    def denials(self) -> List[ClaimPayment]:
        """Claim lines that are appealable denials."""
        return [c for c in self.claims if c.is_denied]


def parse_835(text: str) -> Remittance:
    """Parse X12 835 text into a :class:`Remittance`.

    Segments are terminated by ``~`` and elements separated by ``*`` (the common
    real-world delimiters). Unknown segments are ignored.
    """
    raw = text.replace("\r", "").replace("\n", "")
    segments = [s.strip() for s in raw.split("~") if s.strip()]
    payer = ""
    claims: List[ClaimPayment] = []
    cur: Optional[ClaimPayment] = None

    for seg in segments:
        el = seg.split("*")
        tag = el[0]

        if tag == "N1" and len(el) > 2 and el[1] == "PR":
            payer = el[2].strip()

        elif tag == "CLP":
            if cur is not None:
                claims.append(cur)
            cur = ClaimPayment(
                claim_id=el[1] if len(el) > 1 else "",
                status_code=el[2] if len(el) > 2 else "",
                billed=_num(el, 3),
                paid=_num(el, 4),
                patient_resp=_num(el, 5),
                payer=payer,
            )

        elif tag == "CAS" and cur is not None:
            group = el[1] if len(el) > 1 else ""
            # CAS carries up to six (CARC, amount, quantity) triplets after the group
            i = 2
            while i + 1 < len(el):
                carc = el[i]
                amt = el[i + 1]
                if carc:
                    cur.adjustments.append(Adjustment(group, carc, _to_float(amt)))
                i += 3

        elif tag == "NM1" and cur is not None and len(el) > 1 and el[1] == "QC":
            # QC = patient (subscriber); member id in element 09
            if len(el) > 9:
                cur.member_id = el[9]

        elif tag == "DTM" and cur is not None and len(el) > 2 and el[1] in ("232", "472"):
            cur.dos = el[2]

    if cur is not None:
        claims.append(cur)
    return Remittance(payer=payer, claims=claims)


def _to_float(s: str) -> float:
    try:
        return float(s)
    except (TypeError, ValueError):
        return 0.0


def _num(el: List[str], idx: int) -> float:
    return _to_float(el[idx]) if len(el) > idx and el[idx] else 0.0

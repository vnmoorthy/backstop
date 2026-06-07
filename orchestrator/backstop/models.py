"""Backstop domain models.

Plain dataclasses so they're trivially serializable to the WS event stream and
easy to test. Server-side Pydantic mirrors live in server.py.
See docs/SPEC.md §5 for the authoritative shapes.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import date
from typing import Literal, Optional

DenialCode = Literal["CO-16", "CO-197", "CO-50", "CO-11", "CO-45", "PR-1", "OTHER"]
ModelTier = Literal["local_fast", "mid_reason", "frontier"]
SpecialistKind = Literal[
    "provider_line",
    "billing_office",
    "records_desk",
    "prior_auth_desk",
    "pharmacy_pbm",
    "reconciler",
    "letter_writer",
]


@dataclass
class Denial:
    """A parsed denial. raw_text is frozen verbatim (compliance: never paraphrased)."""

    denial_id: str
    payer: str
    plan: str
    state: str
    denial_code: DenialCode
    cpt: list[str]
    billed_amount: float
    date_of_service: str  # ISO date
    member_id: str
    claim_id: str
    rendering_npi: str
    billing_npi: str
    raw_text: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class AppealSpec:
    """Unsiloed output -> swarm input."""

    denial: Denial
    required_specialists: list[str]
    sol_deadline: str  # ISO date
    parse_confidence: float

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class CallTurn:
    """One utterance in a call. complexity + snr_db drive the PAVO coupling mask."""

    turn_id: int
    speaker: Literal["agent", "rep", "ivr"]
    text: str
    complexity: int = 1  # 1..5
    snr_db: float = 40.0
    ctx_tokens: int = 80
    is_denial_reason: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Rebuttal:
    """A Moss retrieval result: the magic words that overturn this denial."""

    denial_code: str
    payer: str
    magic_words: str
    win_rate: float
    source_id: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Contradiction:
    """The overturning contradiction the reconciler finds across transcripts."""

    claim: str  # what the rep asserted
    evidence: str  # the record that defeats it
    rep_turn_id: int
    evidence_turn_id: int

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class AppealLetter:
    appeal_id: str
    body_md: str
    citations: list[str]
    pdf_path: str = ""
    signed_by_nurse: bool = False

    def to_dict(self) -> dict:
        return asdict(self)

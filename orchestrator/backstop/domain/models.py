"""Core frozen entities and value objects for the Backstop appeals kernel.

These dataclasses are the shared vocabulary every layer speaks. They are pure
and immutable: PHI is only ever held as salted hashes (``member_id_hash``,
``claim_number_hash``), money is integer cents via
:mod:`backstop.domain.money`, and there is no I/O or vendor dependency. The
:class:`Appeal` aggregate carries an append-only ``events`` tuple; legal
transitions are computed by :mod:`backstop.domain.appeal_aggregate`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Optional, Tuple

from backstop.domain.enums import AppealStatus, RouteDecision, Speaker
from backstop.domain.money import Money, RecoverableDollars, SolDeadline

__all__ = [
    "Payer",
    "ClaimLine",
    "Denial",
    "AppealEvent",
    "SignedEvent",
    "Appeal",
    "TurnObservation",
    "CallTurn",
]


@dataclass(frozen=True)
class Payer:
    """An insurance payer (carrier) responsible for adjudicating a claim."""

    payer_id: str
    name: str


@dataclass(frozen=True)
class ClaimLine:
    """A single billed service line on a claim.

    Holds the CPT/HCPCS procedure code, units, and the billed amount in exact
    cents. No PHI lives on a line.
    """

    cpt: str
    units: int
    billed: Money
    modifiers: Tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class Denial:
    """A payer denial of a claim — the input to the appeals pipeline.

    PHI is reduced to salted hashes (``member_id_hash``, ``claim_number_hash``)
    before this entity is built. ``denial_code`` is the CARC (Claim Adjustment
    Reason Code); ``rarc`` is the optional Remittance Advice Remark Code. The
    ``cpt`` tuple lists the procedure codes spanned by the denial.
    """

    denial_id: str
    payer: Payer
    plan: str
    member_id_hash: str
    claim_number_hash: str
    denial_code: str
    rarc: Optional[str]
    cpt: Tuple[str, ...]
    billed: Money
    dos: date
    npis: Tuple[str, ...] = field(default_factory=tuple)
    lines: Tuple[ClaimLine, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class AppealEvent:
    """An immutable, append-only fact in an appeal's history.

    Events are the only way an :class:`Appeal` advances through its state
    machine. ``kind`` names what happened (e.g. ``"denial_parsed"``,
    ``"turn_routed"``, ``"letter_drafted"``); ``seq`` is the monotonic position
    in the per-appeal log; ``at`` is the occurrence date. Subclasses such as
    :class:`SignedEvent` carry transition-gating proof.
    """

    kind: str
    seq: int
    at: date


@dataclass(frozen=True)
class SignedEvent(AppealEvent):
    """The sign-off event that gates the transition into ``FILED``.

    Only the presence of a ``SignedEvent`` permits an appeal to reach
    ``AppealStatus.FILED``; the aggregate refuses to file on any plain
    :class:`AppealEvent`. It carries the SHA-256 of the redacted letter the
    signature covers and the signing nurse's id, so the gate is verifiable.
    """

    nurse_id: str
    letter_sha256: str
    signature_b64: str


@dataclass(frozen=True)
class Appeal:
    """The appeals aggregate: a denial plus its evolving disposition.

    Immutable; transitions produce a *new* ``Appeal`` with an extended
    ``events`` tuple (see :mod:`backstop.domain.appeal_aggregate`). ``route``
    is ``None`` until the routing policy decides; ``needs_human_review`` flags
    low-confidence cases for a nurse. ``status`` is intentionally re-derivable
    from ``events`` but stored for cheap reads.
    """

    id: str
    status: AppealStatus
    denial: Denial
    recoverable: RecoverableDollars
    sol: SolDeadline
    route: Optional[RouteDecision] = None
    needs_human_review: bool = False
    events: Tuple[AppealEvent, ...] = field(default_factory=tuple)

    @property
    def has_signed_event(self) -> bool:
        """Return ``True`` if any recorded event is a :class:`SignedEvent`."""
        return any(isinstance(e, SignedEvent) for e in self.events)


@dataclass(frozen=True)
class TurnObservation:
    """Twelve-dimensional, PHI-free telemetry for one conversational turn.

    Feeds the adaptive model-tier router. It captures acoustic signal
    (``snr``, ``speaking_rate``, ``pitch_var``, ``wada``), device pressure
    (``cpu``, ``ram``, ``battery``, ``gpu``), network conditions (``rtt``,
    ``bw``), and task load (``complexity``, ``ctx_tokens``). Carries telemetry
    only — never transcript content or PHI.
    """

    snr: float
    speaking_rate: float
    pitch_var: float
    wada: float
    cpu: float
    ram: float
    battery: float
    gpu: float
    rtt: float
    bw: float
    complexity: float
    ctx_tokens: int

    def as_vector(self) -> Tuple[float, ...]:
        """Return the observation as a fixed 12-tuple in declaration order."""
        return (
            self.snr,
            self.speaking_rate,
            self.pitch_var,
            self.wada,
            self.cpu,
            self.ram,
            self.battery,
            self.gpu,
            self.rtt,
            self.bw,
            self.complexity,
            float(self.ctx_tokens),
        )


@dataclass(frozen=True)
class CallTurn:
    """One turn in a payer phone call: who spoke and its telemetry.

    Pairs the :class:`Speaker` with the :class:`TurnObservation` for that turn
    and its monotonic ``index`` in the call. No transcript text or PHI is held
    here; spoken content is handled separately under the redaction boundary.
    """

    index: int
    speaker: Speaker
    observation: TurnObservation

"""JSON (de)serialisation of the :class:`Appeal` aggregate for persistence.

The SQLite repo stores each aggregate as a JSON blob plus a few indexed columns.
This module owns the *only* mapping between an :class:`~backstop.domain.models.Appeal`
and its on-disk JSON form so the encode/decode round-trip lives in one place and
stays exact (integer cents, salted hashes, append-only events, enum values).

Pure: standard library plus :mod:`backstop.domain` only. No I/O, no vendor SDK.
"""

from __future__ import annotations

import json
from datetime import date
from typing import Any, Dict

from backstop.domain.enums import AppealStatus, RouteDecision
from backstop.domain.models import (
    Appeal,
    AppealEvent,
    ClaimLine,
    Denial,
    Payer,
    SignedEvent,
)
from backstop.domain.money import Money, RecoverableDollars, SolDeadline

__all__ = ["encode_appeal", "decode_appeal"]


def encode_appeal(appeal: Appeal) -> str:
    """Serialise ``appeal`` to a deterministic JSON string."""
    return json.dumps(_appeal_to_dict(appeal), sort_keys=True, separators=(",", ":"))


def decode_appeal(blob: str) -> Appeal:
    """Rebuild an :class:`Appeal` from a JSON string produced by :func:`encode_appeal`."""
    return _appeal_from_dict(json.loads(blob))


def _appeal_to_dict(appeal: Appeal) -> Dict[str, Any]:
    """Return the plain-dict form of an :class:`Appeal`."""
    return {
        "id": appeal.id,
        "status": appeal.status.value,
        "denial": _denial_to_dict(appeal.denial),
        "recoverable": appeal.recoverable.cents,
        "sol": appeal.sol.iso,
        "route": appeal.route.value if appeal.route is not None else None,
        "needs_human_review": appeal.needs_human_review,
        "events": [_event_to_dict(event) for event in appeal.events],
    }


def _appeal_from_dict(data: Dict[str, Any]) -> Appeal:
    """Rebuild an :class:`Appeal` from its plain-dict form."""
    route_raw = data["route"]
    return Appeal(
        id=data["id"],
        status=AppealStatus(data["status"]),
        denial=_denial_from_dict(data["denial"]),
        recoverable=RecoverableDollars(Money(int(data["recoverable"]))),
        sol=SolDeadline.from_iso(data["sol"]),
        route=RouteDecision(route_raw) if route_raw is not None else None,
        needs_human_review=bool(data["needs_human_review"]),
        events=tuple(_event_from_dict(item) for item in data["events"]),
    )


def _denial_to_dict(denial: Denial) -> Dict[str, Any]:
    """Return the plain-dict form of a :class:`Denial`."""
    return {
        "denial_id": denial.denial_id,
        "payer": {"payer_id": denial.payer.payer_id, "name": denial.payer.name},
        "plan": denial.plan,
        "member_id_hash": denial.member_id_hash,
        "claim_number_hash": denial.claim_number_hash,
        "denial_code": denial.denial_code,
        "rarc": denial.rarc,
        "cpt": list(denial.cpt),
        "billed": denial.billed.cents,
        "dos": denial.dos.isoformat(),
        "npis": list(denial.npis),
        "lines": [_line_to_dict(line) for line in denial.lines],
    }


def _denial_from_dict(data: Dict[str, Any]) -> Denial:
    """Rebuild a :class:`Denial` from its plain-dict form."""
    return Denial(
        denial_id=data["denial_id"],
        payer=Payer(payer_id=data["payer"]["payer_id"], name=data["payer"]["name"]),
        plan=data["plan"],
        member_id_hash=data["member_id_hash"],
        claim_number_hash=data["claim_number_hash"],
        denial_code=data["denial_code"],
        rarc=data["rarc"],
        cpt=tuple(data["cpt"]),
        billed=Money(int(data["billed"])),
        dos=date.fromisoformat(data["dos"]),
        npis=tuple(data["npis"]),
        lines=tuple(_line_from_dict(item) for item in data["lines"]),
    )


def _line_to_dict(line: ClaimLine) -> Dict[str, Any]:
    """Return the plain-dict form of a :class:`ClaimLine`."""
    return {
        "cpt": line.cpt,
        "units": line.units,
        "billed": line.billed.cents,
        "modifiers": list(line.modifiers),
    }


def _line_from_dict(data: Dict[str, Any]) -> ClaimLine:
    """Rebuild a :class:`ClaimLine` from its plain-dict form."""
    return ClaimLine(
        cpt=data["cpt"],
        units=int(data["units"]),
        billed=Money(int(data["billed"])),
        modifiers=tuple(data["modifiers"]),
    )


def _event_to_dict(event: AppealEvent) -> Dict[str, Any]:
    """Return the plain-dict form of an :class:`AppealEvent` (signed or plain)."""
    base: Dict[str, Any] = {
        "kind": event.kind,
        "seq": event.seq,
        "at": event.at.isoformat(),
    }
    if isinstance(event, SignedEvent):
        base["__signed__"] = True
        base["nurse_id"] = event.nurse_id
        base["letter_sha256"] = event.letter_sha256
        base["signature_b64"] = event.signature_b64
    return base


def _event_from_dict(data: Dict[str, Any]) -> AppealEvent:
    """Rebuild an :class:`AppealEvent`/:class:`SignedEvent` from its dict form."""
    kind = data["kind"]
    seq = int(data["seq"])
    at = date.fromisoformat(data["at"])
    if data.get("__signed__"):
        return SignedEvent(
            kind=kind,
            seq=seq,
            at=at,
            nurse_id=data["nurse_id"],
            letter_sha256=data["letter_sha256"],
            signature_b64=data["signature_b64"],
        )
    return AppealEvent(kind=kind, seq=seq, at=at)

"""SignoffService — the compliance gate that files an appeal.

An appeal reaches ``FILED`` only when **both** conditions hold:

1. :meth:`AuditLogPort.verify_chain` returns ``True`` for the appeal — the
   tamper-evident hash chain over its model calls is intact, and
2. :meth:`SignaturePort.verify` confirms the supplied :class:`Signature` is a
   valid signature over the redacted letter hash.

Only then does the service atomically CAS the appeal from ``AWAITING_SIGNOFF``
to ``FILED`` via :meth:`AppealRepositoryPort.update_status_atomic`. If either
check fails, or the CAS loses to a concurrent writer, the appeal is **not**
filed and the reason is reported. This is the single place an appeal can be
filed; there is no bypass.

The service depends only on ports and raises only domain errors.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum
from typing import Optional

from backstop.domain.enums import AppealStatus
from backstop.ports.appeal_repository_port import (
    AppealEventRecord,
    AppealRepositoryPort,
)
from backstop.ports.audit_log_port import AuditLogPort
from backstop.ports.clock_port import ClockPort
from backstop.ports.signature_port import Signature, SignaturePort

__all__ = ["SignoffRefusal", "SignoffResult", "SignoffService"]


class SignoffRefusal(str, Enum):
    """Why a sign-off was refused (when it was)."""

    AUDIT_CHAIN_BROKEN = "AUDIT_CHAIN_BROKEN"
    BAD_SIGNATURE = "BAD_SIGNATURE"
    STALE_STATE = "STALE_STATE"


@dataclass(frozen=True)
class SignoffResult:
    """The outcome of an attempted sign-off.

    Attributes:
        filed: Whether the appeal transitioned to ``FILED``.
        appeal_id: The appeal the attempt targeted.
        refusal: The refusal reason when ``filed`` is ``False``; else ``None``.
    """

    filed: bool
    appeal_id: str
    refusal: Optional[SignoffRefusal] = None


class SignoffService:
    """File an appeal only on an intact audit chain and a valid signature."""

    def __init__(
        self,
        *,
        repo: AppealRepositoryPort,
        audit: AuditLogPort,
        signature: SignaturePort,
        clock: ClockPort,
    ) -> None:
        """Store the repository, audit chain, signature, and clock ports."""
        self._repo = repo
        self._audit = audit
        self._signature = signature
        self._clock = clock

    async def sign_off(
        self,
        appeal_id: str,
        *,
        appeal_hash: bytes,
        signature: Signature,
        seq: int,
    ) -> SignoffResult:
        """Attempt to file ``appeal_id``; refuse unless both gate checks pass.

        Order of checks (cheapest-first, both mandatory):

        1. ``audit.verify_chain(appeal_id)`` must be ``True``.
        2. ``signature.verify(appeal_hash, signature)`` must be ``True``.

        Only then is the ``AWAITING_SIGNOFF -> FILED`` CAS attempted. A lost CAS
        is reported as a stale-state refusal (no event is written).
        """
        if not self._audit.verify_chain(appeal_id):
            return self._refuse(appeal_id, SignoffRefusal.AUDIT_CHAIN_BROKEN)

        if not self._signature.verify(appeal_hash, signature):
            return self._refuse(appeal_id, SignoffRefusal.BAD_SIGNATURE)

        won = await self._repo.update_status_atomic(
            appeal_id,
            AppealStatus.AWAITING_SIGNOFF,
            AppealStatus.FILED,
        )
        if not won:
            return self._refuse(appeal_id, SignoffRefusal.STALE_STATE)

        await self._repo.append_event(
            appeal_id,
            self._signed_event(appeal_id, signature, seq),
        )
        return SignoffResult(filed=True, appeal_id=appeal_id)

    @staticmethod
    def _refuse(appeal_id: str, refusal: SignoffRefusal) -> SignoffResult:
        """Build a not-filed result carrying ``refusal``."""
        return SignoffResult(filed=False, appeal_id=appeal_id, refusal=refusal)

    def _signed_event(
        self,
        appeal_id: str,
        signature: Signature,
        seq: int,
    ) -> AppealEventRecord:
        """Build the append-only ``filed`` event (non-PHI signature metadata)."""
        payload = json.dumps(
            {
                "nurse": signature.nurse_identity,
                "public_key_id": signature.public_key_id,
                "signed_at": signature.signed_at_iso,
            },
            separators=(",", ":"),
        )
        return AppealEventRecord(
            appeal_id=appeal_id,
            seq=seq,
            kind="filed",
            ts_iso=self._clock.now().isoformat(),
            payload_json=payload,
        )

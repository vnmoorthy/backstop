"""IngestDenialService — admission-capped, audit-wrapped denial extraction.

Parsing one denial artifact is wrapped in three disciplines:

* **Admission cap** — the parse runs inside ``async with gate.slot(...)`` so the
  number of simultaneous extractions never exceeds the concurrency cap.
* **EDI -> sim fallback** — the real parser declares (via ``supports``) that it
  cannot handle raw EDI (835/837/277); the service then re-routes the same bytes
  to the deterministic sim parser. Either way an extraction is produced and no
  unsupported artifact reaches the real adapter.
* **Audit** — every extraction appends a tamper-evident :class:`AuditRecord`
  (hashes only, never raw bytes) to the chain for the appeal.

The artifact crosses as validated in-memory bytes only — no server-side path is
ever derived from caller input. The service depends only on ports.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Tuple

from backstop.domain.enums import IntegrationMode
from backstop.ports.audit_log_port import AuditLogPort, AuditRecord
from backstop.ports.concurrency_gate_port import ConcurrencyGatePort
from backstop.ports.denial_parser_port import (
    DenialExtraction,
    DenialParserPort,
    ParseRequest,
)

__all__ = ["IngestResult", "IngestDenialService"]


def _sha256_hex(data: bytes) -> str:
    """Return the hex SHA-256 of ``data`` (used for audit, never raw storage)."""
    return hashlib.sha256(data).hexdigest()


@dataclass(frozen=True)
class IngestResult:
    """The outcome of ingesting one denial artifact.

    Attributes:
        extraction: The structured, provenanced extraction.
        used_fallback: Whether the sim parser served after the real refused EDI.
        audit_id: The audit-chain id assigned to the append.
    """

    extraction: DenialExtraction
    used_fallback: bool
    audit_id: str


class IngestDenialService:
    """Parse a denial artifact under the gate, with EDI fallback and audit."""

    def __init__(
        self,
        *,
        gate: ConcurrencyGatePort,
        primary_parser: DenialParserPort,
        fallback_parser: DenialParserPort,
        audit: AuditLogPort,
    ) -> None:
        """Store the gate, the real + sim parsers, and the audit chain."""
        self._gate = gate
        self._primary = primary_parser
        self._fallback = fallback_parser
        self._audit = audit

    async def ingest(
        self,
        appeal_id: str,
        req: ParseRequest,
    ) -> IngestResult:
        """Parse ``req`` for ``appeal_id`` under the gate, with audit + fallback.

        The whole parse (including any fallback) runs inside one admission slot,
        so concurrent ingests are capped. An :class:`AuditRecord` is appended
        on success.
        """
        async with self._gate.slot(appeal_id):
            extraction, used_fallback = await self._parse_with_fallback(req)
            audit_id = self._audit.append(
                self._audit_record(appeal_id, req, used_fallback)
            )
        return IngestResult(
            extraction=extraction,
            used_fallback=used_fallback,
            audit_id=audit_id,
        )

    async def _parse_with_fallback(
        self, req: ParseRequest
    ) -> Tuple[DenialExtraction, bool]:
        """Parse with the primary parser; fall back to the sim on EDI refusal."""
        if self._primary.supports(req.kind):
            return await self._primary.parse(req), False
        return await self._fallback.parse(req), True

    def _audit_record(
        self,
        appeal_id: str,
        req: ParseRequest,
        used_fallback: bool,
    ) -> AuditRecord:
        """Build a hashes-only audit record for one ingest (no raw bytes)."""
        mode = IntegrationMode.SIM if used_fallback else IntegrationMode.REAL
        content_hash = _sha256_hex(req.content)
        return AuditRecord(
            appeal_id=appeal_id,
            stage="ingest_denial",
            model="denial_parser",
            mode=mode,
            prompt_sha256=content_hash,
            completion_sha256=content_hash,
            redaction_count=0,
            prompt_tokens=0,
            completion_tokens=0,
            usd_micros=0,
            gateway_request_id=req.filename,
        )

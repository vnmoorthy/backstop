"""AuditLogPort — TrueFoundry tamper-evident audit chain (L2 port).

A SHA-256 hash chain over every model call: each record carries the prior record's
hash so ``verify_chain`` can recompute and detect any field flip. The audit store
is append-only (no UPDATE/DELETE) and persists only hashes of redacted text, never
raw bodies or keys. The sign-off gate requires ``verify_chain() == True`` before a
letter can be signed and an appeal filed.

This module defines the Protocol plus its ``AuditRecord`` DTO only; concrete
adapters live in ``backstop.adapters.truefoundry``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional, Protocol, runtime_checkable

from backstop.domain.enums import IntegrationMode

__all__ = [
    "AuditRecord",
    "AuditLogPort",
]


@dataclass(frozen=True)
class AuditRecord:
    """One append-only, hash-chained audit row for a model call.

    Stores only SHA-256 hashes of redacted text — never raw prompts, completions,
    or keys. ``prev_hash`` and ``record_hash`` are populated by the chain on append.

    Attributes:
        appeal_id: Surrogate appeal identifier this call belongs to.
        stage: Pipeline stage label (e.g. ``synthesize_rebuttal``).
        model: The model that served the call.
        mode: Whether the call ran against a real or sim backend.
        prompt_sha256: Hash of the redacted prompt text.
        completion_sha256: Hash of the redacted completion text.
        redaction_count: Number of PHI spans masked across both legs.
        prompt_tokens: Counted prompt tokens.
        completion_tokens: Counted completion tokens.
        usd_micros: Priced cost of the call, in integer USD micros.
        gateway_request_id: Upstream correlation id, when reported.
        prev_hash: Hash of the prior chain record (set on append).
        record_hash: Hash of this record (set on append).
    """

    appeal_id: str
    stage: str
    model: str
    mode: IntegrationMode
    prompt_sha256: str
    completion_sha256: str
    redaction_count: int
    prompt_tokens: int
    completion_tokens: int
    usd_micros: int
    gateway_request_id: Optional[str] = None
    prev_hash: Optional[str] = None
    record_hash: Optional[str] = None


@runtime_checkable
class AuditLogPort(Protocol):
    """Append-only, tamper-evident hash chain over model-call records."""

    def append(self, record: AuditRecord) -> str:
        """Append ``record`` to the chain and return its assigned audit id."""
        ...

    def verify_chain(self, appeal_id: Optional[str] = None) -> bool:
        """Recompute the chain and return whether it is intact.

        Scoped to ``appeal_id`` when provided, otherwise the whole chain.
        """
        ...

    def iter(self, appeal_id: str) -> Iterable[AuditRecord]:
        """Iterate this appeal's audit records in append order."""
        ...

"""Shared gateway plumbing — token counting, hashing, and the audit/cost legs.

Both gateway adapters (sim and real) share the same redact-in / audit / cost
tail: given an already-redacted prompt and a raw upstream completion, they
re-redact the completion, count tokens, price the call against the shared ledger,
and append one hash-chained audit record. Centralising that here keeps each
adapter to its one job — the sim composes a local completion, the real one talks
to the gateway — while guaranteeing both honour the identical accounting contract
against the SAME injected redaction/audit/cost singletons.

This module is stdlib + domain + sibling-adapter only; no vendor SDK.
"""

from __future__ import annotations

import hashlib
import re
from typing import List, Optional, Tuple

from backstop.domain.enums import IntegrationMode
from backstop.domain.money import Money
from backstop.domain.redacted import RedactedText
from backstop.ports.audit_log_port import AuditLogPort, AuditRecord
from backstop.ports.cost_ledger_port import CostLedgerPort
from backstop.ports.llm_gateway_port import GatewayMessage, LLMResponse
from backstop.ports.redaction_port import RedactionPort

__all__ = [
    "estimate_tokens",
    "sha256_text",
    "AccountedCall",
    "finalize_call",
]

# Word/punctuation token splitter for the deterministic word/4-style estimate
# used when tiktoken is unavailable. Splitting on word + standalone punctuation
# tracks output length far better than a raw character count.
_TOKEN_RE = re.compile(r"\w+|[^\w\s]")


def estimate_tokens(text: str) -> int:
    """Return a deterministic, length-tracking token estimate for *text*.

    Tries ``tiktoken`` lazily for a real BPE count; falls back to a
    word/punctuation heuristic (~4 chars/token) otherwise. Always ``>= 1`` for
    non-empty text so a priced call never reports zero output tokens.
    """
    if not text:
        return 0
    bpe = _tiktoken_count(text)
    if bpe is not None:
        return bpe
    pieces = _TOKEN_RE.findall(text)
    # ~0.75 tokens per whitespace/punct piece approximates BPE on English.
    return max(1, (len(pieces) * 3 + 3) // 4)


def _tiktoken_count(text: str) -> Optional[int]:
    """Return a real BPE token count via ``tiktoken``, or ``None`` if absent.

    Imported via :func:`importlib.import_module` (not a static ``import``) so the
    module type-checks and imports cleanly whether or not the optional real-mode
    dependency is installed.
    """
    try:
        import importlib

        tiktoken = importlib.import_module("tiktoken")
        enc = tiktoken.get_encoding("cl100k_base")
        return max(1, len(enc.encode(text)))
    except Exception:  # - any failure -> deterministic heuristic
        return None


def sha256_text(text: str) -> str:
    """Return the hex SHA-256 of *text* (used for audit, never stores raw text)."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class AccountedCall:
    """The redact-in / audit / cost tail shared by both gateway adapters."""

    def __init__(
        self,
        *,
        redaction: RedactionPort,
        audit: AuditLogPort,
        cost: CostLedgerPort,
        mode: IntegrationMode,
    ) -> None:
        """Hold the injected singletons and the adapter's mode."""
        self._redaction = redaction
        self._audit = audit
        self._cost = cost
        self._mode = mode

    def redacted_prompt_text(self, messages: Tuple[GatewayMessage, ...]) -> str:
        """Flatten redacted messages into the prompt string sent upstream."""
        return "\n".join(f"{m.role}: {m.content.text}" for m in messages)


def _count_redactions(prompt: str, completion: RedactedText) -> int:
    """Count PHI placeholders masked across both legs of the call."""
    # Placeholders look like ``[TAG]``; count them in the redacted prompt text and
    # the re-redacted completion spans.
    placeholder_re = re.compile(r"\[[A-Z_]+\]")
    return len(placeholder_re.findall(prompt)) + len(completion.spans)


def finalize_call(
    *,
    redaction: RedactionPort,
    audit: AuditLogPort,
    cost: CostLedgerPort,
    mode: IntegrationMode,
    appeal_id: str,
    stage: str,
    model: str,
    redacted_prompt_text: str,
    raw_completion: str,
    finish_reason: Optional[str],
    gateway_request_id: Optional[str],
) -> LLMResponse:
    """Re-redact the completion, price it, append the audit row, and build a response.

    This is the single accounting tail both adapters call after they have a raw
    completion (locally composed for sim, upstream-returned for real). It mints
    the PHI-free ``LLMResponse`` and is the only place that writes to the audit
    and cost sinks for a successful call.
    """
    # (3) Defence-in-depth: re-redact the completion through the SAME redactor.
    redacted_completion = redaction.redact_text(raw_completion)

    prompt_tokens = estimate_tokens(redacted_prompt_text)
    completion_tokens = estimate_tokens(redacted_completion.text)

    # (4) Price the usage into the shared ledger; returns Money (cents).
    money: Money = cost.record(
        appeal_id=appeal_id,
        stage=stage,
        model=model,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
    )
    # Re-express the priced cost in integer USD micros for the audit row, taking
    # the ledger's own micros pricing when available so the row matches the ledger.
    usd_micros = _usd_micros(cost, model, prompt_tokens, completion_tokens, money)

    redaction_count = _count_redactions(redacted_prompt_text, redacted_completion)

    # (5) Append the hash-chained audit record — hashes only, never raw text.
    record = AuditRecord(
        appeal_id=appeal_id,
        stage=stage,
        model=model,
        mode=mode,
        prompt_sha256=sha256_text(redacted_prompt_text),
        completion_sha256=sha256_text(redacted_completion.text),
        redaction_count=redaction_count,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        usd_micros=usd_micros,
        gateway_request_id=gateway_request_id,
    )
    audit.append(record)

    # (6) Return a PHI-free response.
    return LLMResponse(
        text=redacted_completion,
        model=model,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        cost=money,
        finish_reason=finish_reason,
        gateway_request_id=gateway_request_id,
    )


def _usd_micros(
    cost: CostLedgerPort,
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
    money: Money,
) -> int:
    """Return the call cost in integer USD micros for the audit row.

    Prefers the ledger's own integer-micros pricing (so the audited figure equals
    the ledger's exact pre-rounding cost); falls back to the rounded ``Money``
    cents expressed in micros for ledgers that do not expose micros pricing.
    """
    price_tokens = getattr(cost, "price_tokens", None)
    if callable(price_tokens):
        result = price_tokens(model, prompt_tokens, completion_tokens)
        if isinstance(result, int):
            return result
    return money.cents * 10_000


def chunk_words(text: str, group: int) -> List[str]:
    """Split *text* into whitespace-joined word groups of size *group*.

    Used by the streaming paths to emit token-grouped chunks. Always yields at
    least one chunk for non-empty text.
    """
    words = text.split()
    if not words:
        return [text] if text else []
    out: List[str] = []
    for i in range(0, len(words), max(1, group)):
        out.append(" ".join(words[i : i + max(1, group)]))
    return out

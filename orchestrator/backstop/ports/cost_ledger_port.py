"""CostLedgerPort — TrueFoundry priced-cost ledger (L2 port).

A genuinely-computed, integer-micros cost ledger shared by real and sim alike
(the sim path prices the same token/char usage with the same table). Costs are
recorded per model call and per character for char-billed vendors (Qwen TTS), and
aggregated by appeal for the triage worklist and per-appeal timeline. No floats:
all money is integer USD micros behind the ``Money`` value object.

This module defines the Protocol plus its ``CostSnapshot`` DTO only; concrete
adapters live in ``backstop.adapters.truefoundry``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Optional, Protocol, runtime_checkable

from backstop.domain.money import Money

__all__ = [
    "CostSnapshot",
    "CostLedgerPort",
]


@dataclass(frozen=True)
class CostSnapshot:
    """Aggregated ledger spend, optionally scoped to one appeal.

    Attributes:
        total: Total priced cost across the included records.
        appeal_id: The appeal this snapshot is scoped to, or ``None`` for all.
        by_vendor: Per-vendor cost breakdown (e.g. ``truefoundry``, ``qwen``).
        by_stage: Per-stage cost breakdown.
    """

    total: Money
    appeal_id: Optional[str] = None
    by_vendor: Dict[str, Money] = field(default_factory=dict)
    by_stage: Dict[str, Money] = field(default_factory=dict)


@runtime_checkable
class CostLedgerPort(Protocol):
    """Integer-micros priced cost ledger, aggregated per appeal."""

    def record(
        self,
        appeal_id: str,
        stage: str,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
    ) -> Money:
        """Price and record a token-billed call; return its computed cost."""
        ...

    def record_chars(self, appeal_id: str, vendor: str, chars: int) -> Money:
        """Price and record a char-billed call (e.g. TTS); return its cost."""
        ...

    def snapshot(self, appeal_id: Optional[str] = None) -> CostSnapshot:
        """Return aggregated spend, scoped to ``appeal_id`` when provided."""
        ...

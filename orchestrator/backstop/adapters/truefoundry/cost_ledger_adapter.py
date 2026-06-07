"""CostLedgerAdapter — priced, integer-micros token/char ledger (CostLedgerPort).

A genuinely-computed cost ledger shared by the real and sim gateways alike: both
price the SAME token/char usage with the SAME per-model table, so dashboard
numbers are comparable across modes. No floats touch the money path — every price
is expressed in integer USD micros (1e-6 USD) and only converted to the
cents-based :class:`~backstop.domain.money.Money` value object at the boundary,
rounding half-up.

Pricing:

* token-billed calls: ``in_micros_per_1k`` and ``out_micros_per_1k`` per model,
  with a configurable default for unknown models;
* char-billed calls (e.g. Qwen TTS): ``micros_per_1k_chars`` per vendor.

The ledger keeps an in-memory list of priced line items and aggregates them on
demand by appeal, vendor, and stage. It is self-contained (stdlib + domain only)
and adds no vendor dependency.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Mapping, Optional

from backstop.domain.money import Money
from backstop.ports.cost_ledger_port import CostSnapshot

__all__ = [
    "CostLedgerAdapter",
    "TokenPrice",
    "DEFAULT_TOKEN_PRICES",
    "DEFAULT_CHAR_PRICES",
    "DEFAULT_TOKEN_PRICE",
    "TFY_VENDOR",
]

# Logical vendor label for gateway (TrueFoundry) token spend.
TFY_VENDOR: str = "truefoundry"


@dataclass(frozen=True)
class TokenPrice:
    """Per-1K-token price for one model, in integer USD micros.

    Attributes:
        in_micros_per_1k: USD micros charged per 1,000 prompt tokens.
        out_micros_per_1k: USD micros charged per 1,000 completion tokens.
    """

    in_micros_per_1k: int
    out_micros_per_1k: int


# Default per-model price table (USD micros / 1K tokens; 1 USD == 1_000_000
# micros). The gpt-4o-mini row matches the sponsor spec's worked example:
# in=$0.15/1K (150_000 micros), out=$0.60/1K (600_000 micros), so usage
# {1000 prompt, 500 completion} prices to 150_000 + 300_000 = 450_000 micros,
# i.e. $0.45 == 45 cents.
DEFAULT_TOKEN_PRICES: Dict[str, TokenPrice] = {
    "openai-main/gpt-4o-mini": TokenPrice(in_micros_per_1k=150_000, out_micros_per_1k=600_000),
    "gpt-4o-mini": TokenPrice(in_micros_per_1k=150_000, out_micros_per_1k=600_000),
    "openai-main/gpt-4o": TokenPrice(in_micros_per_1k=2_500_000, out_micros_per_1k=10_000_000),
    "minimax/abab6.5s": TokenPrice(in_micros_per_1k=200_000, out_micros_per_1k=200_000),
}

# Fallback price for an unknown model — never zero, so an unpriced call still
# shows up as real spend on the dashboard.
DEFAULT_TOKEN_PRICE: TokenPrice = TokenPrice(
    in_micros_per_1k=150_000, out_micros_per_1k=600_000
)

# Per-vendor char price table (USD micros / 1K characters) for char-billed
# vendors such as Qwen TTS (here $0.04 / 1K chars == 40_000 micros).
DEFAULT_CHAR_PRICES: Dict[str, int] = {
    "qwen": 40_000,
    "qwen-tts": 40_000,
}

# Fallback char price for an unknown char-billed vendor.
_DEFAULT_CHAR_PRICE: int = 40_000


@dataclass(frozen=True)
class _LineItem:
    """One priced ledger entry, in integer USD micros."""

    appeal_id: str
    vendor: str
    stage: str
    micros: int


def _micros_to_money(micros: int) -> Money:
    """Convert integer USD micros to :class:`Money` cents, rounding half-up.

    1 cent == 10,000 micros. Half-up rounding keeps the ledger's reported cents
    monotonic and never silently truncates sub-cent spend to zero on aggregation.
    """
    sign = -1 if micros < 0 else 1
    cents = (abs(micros) + 5_000) // 10_000
    return Money(sign * cents)


class CostLedgerAdapter:
    """Integer-micros priced cost ledger, aggregated per appeal.

    Implements :class:`~backstop.ports.cost_ledger_port.CostLedgerPort`. The
    returned :class:`Money` is the cents rounding of the exact micros priced for
    the call; aggregation sums the exact micros first and rounds once, so totals
    do not accumulate per-call rounding drift.
    """

    def __init__(
        self,
        *,
        token_prices: Optional[Mapping[str, TokenPrice]] = None,
        char_prices: Optional[Mapping[str, int]] = None,
        default_token_price: TokenPrice = DEFAULT_TOKEN_PRICE,
        vendor: str = TFY_VENDOR,
    ) -> None:
        """Build the ledger over an optional custom price table."""
        self._token_prices: Dict[str, TokenPrice] = dict(
            token_prices if token_prices is not None else DEFAULT_TOKEN_PRICES
        )
        self._char_prices: Dict[str, int] = dict(
            char_prices if char_prices is not None else DEFAULT_CHAR_PRICES
        )
        self._default_token_price = default_token_price
        self._vendor = vendor
        self._items: List[_LineItem] = []

    # ----------------------------------------------------------------- #
    # CostLedgerPort.
    # ----------------------------------------------------------------- #
    def record(
        self,
        appeal_id: str,
        stage: str,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
    ) -> Money:
        """Price and record a token-billed call; return its computed cost."""
        micros = self.price_tokens(model, prompt_tokens, completion_tokens)
        self._items.append(
            _LineItem(appeal_id=appeal_id, vendor=self._vendor, stage=stage, micros=micros)
        )
        return _micros_to_money(micros)

    def record_chars(self, appeal_id: str, vendor: str, chars: int) -> Money:
        """Price and record a char-billed call (e.g. TTS); return its cost."""
        micros = self.price_chars(vendor, chars)
        self._items.append(
            _LineItem(appeal_id=appeal_id, vendor=vendor, stage="speech", micros=micros)
        )
        return _micros_to_money(micros)

    def snapshot(self, appeal_id: Optional[str] = None) -> CostSnapshot:
        """Return aggregated spend, scoped to *appeal_id* when provided."""
        total_micros = 0
        by_vendor_micros: Dict[str, int] = {}
        by_stage_micros: Dict[str, int] = {}
        for item in self._items:
            if appeal_id is not None and item.appeal_id != appeal_id:
                continue
            total_micros += item.micros
            by_vendor_micros[item.vendor] = by_vendor_micros.get(item.vendor, 0) + item.micros
            by_stage_micros[item.stage] = by_stage_micros.get(item.stage, 0) + item.micros
        return CostSnapshot(
            total=_micros_to_money(total_micros),
            appeal_id=appeal_id,
            by_vendor={v: _micros_to_money(m) for v, m in by_vendor_micros.items()},
            by_stage={s: _micros_to_money(m) for s, m in by_stage_micros.items()},
        )

    # ----------------------------------------------------------------- #
    # Pricing primitives (integer micros; exposed for the gateway audit row).
    # ----------------------------------------------------------------- #
    def price_tokens(self, model: str, prompt_tokens: int, completion_tokens: int) -> int:
        """Return the priced cost of a token-billed call in integer USD micros."""
        price = self._token_prices.get(model, self._default_token_price)
        # micros = tokens * micros_per_1k / 1000, kept exact via integer division
        # by 1000 with round-half-up so sub-1K usage still prices to whole micros.
        in_micros = (prompt_tokens * price.in_micros_per_1k + 500) // 1000
        out_micros = (completion_tokens * price.out_micros_per_1k + 500) // 1000
        return in_micros + out_micros

    def price_chars(self, vendor: str, chars: int) -> int:
        """Return the priced cost of a char-billed call in integer USD micros."""
        per_1k = self._char_prices.get(vendor, _DEFAULT_CHAR_PRICE)
        return (chars * per_1k + 500) // 1000

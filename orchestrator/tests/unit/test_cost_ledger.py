"""Unit tests for the priced, integer-micros cost ledger adapter.

The ledger is a first-class output of the gateway: both real and sim adapters
price the SAME token/char usage with the SAME table so dashboard numbers are
comparable. These tests pin the sponsor-spec worked example (usage {1000, 500}
with gpt-4o-mini pricing == $0.45), assert per-appeal scoping sums only that
appeal, and confirm cost is genuinely computed (never zero / never a float).
"""

from __future__ import annotations

from backstop.adapters.truefoundry.cost_ledger_adapter import (
    CostLedgerAdapter,
    TokenPrice,
)
from backstop.domain.money import Money
from backstop.ports.cost_ledger_port import CostLedgerPort, CostSnapshot

_GPT_4O_MINI = "openai-main/gpt-4o-mini"


def _ledger() -> CostLedgerAdapter:
    """Return a fresh ledger over the default price table."""
    return CostLedgerAdapter()


# --------------------------------------------------------------------------- #
# Type contract.
# --------------------------------------------------------------------------- #
def test_adapter_satisfies_cost_port() -> None:
    """The adapter is a structural ``CostLedgerPort``."""
    assert isinstance(_ledger(), CostLedgerPort)


# --------------------------------------------------------------------------- #
# The sponsor-spec worked example.
# --------------------------------------------------------------------------- #
def test_prices_spec_example_to_45_cents() -> None:
    """usage {1000, 500} with in=$0.15/1K, out=$0.60/1K prices to $0.45."""
    ledger = _ledger()
    cost = ledger.record("ap1", "draft_letter", _GPT_4O_MINI, 1000, 500)
    assert isinstance(cost, Money)
    assert cost.cents == 45  # 0.15*1 + 0.60*0.5 == 0.45 USD
    assert cost.format() == "$0.45"


def test_price_tokens_returns_integer_micros() -> None:
    """The micros pricing primitive is exact integer USD micros."""
    micros = _ledger().price_tokens(_GPT_4O_MINI, 1000, 500)
    assert micros == 450_000
    assert isinstance(micros, int)


def test_cost_is_money_value_object_not_float() -> None:
    """Recorded cost is a ``Money`` (integer cents), never a float."""
    cost = _ledger().record("ap1", "compose_line", _GPT_4O_MINI, 312, 144)
    assert isinstance(cost.cents, int)
    assert not isinstance(cost.cents, float)


def test_cost_is_positive_for_real_usage() -> None:
    """Any non-trivial usage yields strictly positive spend (never faked zero)."""
    cost = _ledger().record("ap1", "synthesize_rebuttal", _GPT_4O_MINI, 500, 200)
    assert cost.cents > 0


# --------------------------------------------------------------------------- #
# Aggregation + per-appeal scoping.
# --------------------------------------------------------------------------- #
def test_snapshot_sums_only_the_scoped_appeal() -> None:
    """``snapshot(appeal_id)`` sums only that appeal's records."""
    ledger = _ledger()
    ledger.record("ap1", "draft_letter", _GPT_4O_MINI, 1000, 500)  # $0.45
    ledger.record("ap2", "draft_letter", _GPT_4O_MINI, 1000, 500)  # $0.45
    ledger.record("ap1", "compose_line", _GPT_4O_MINI, 1000, 500)  # $0.45

    snap1 = ledger.snapshot("ap1")
    assert isinstance(snap1, CostSnapshot)
    assert snap1.appeal_id == "ap1"
    assert snap1.total.cents == 90  # two ap1 calls

    snap2 = ledger.snapshot("ap2")
    assert snap2.total.cents == 45

    snap_all = ledger.snapshot()
    assert snap_all.appeal_id is None
    assert snap_all.total.cents == 135  # all three calls


def test_snapshot_breaks_down_by_stage_and_vendor() -> None:
    """The snapshot exposes per-stage and per-vendor breakdowns."""
    ledger = _ledger()
    ledger.record("ap1", "draft_letter", _GPT_4O_MINI, 1000, 500)
    ledger.record("ap1", "compose_line", _GPT_4O_MINI, 1000, 500)
    snap = ledger.snapshot("ap1")
    assert snap.by_stage["draft_letter"].cents == 45
    assert snap.by_stage["compose_line"].cents == 45
    assert snap.by_vendor["truefoundry"].cents == 90


def test_aggregation_rounds_once_not_per_call() -> None:
    """Sub-cent calls aggregate from exact micros, then round once.

    Three calls of 1 prompt token each price to a few hundred micros apiece
    (well under a cent); individually they round to $0.00, but their exact-micros
    sum must still be reflected — the ledger does not lose sub-cent spend by
    rounding per call before summing.
    """
    ledger = _ledger()
    # 1 prompt token at $0.15/1K == 150 micros; 30 such calls == 4500 micros.
    for _ in range(30):
        ledger.record("ap1", "compose_line", _GPT_4O_MINI, 1, 0)
    snap = ledger.snapshot("ap1")
    # 4500 micros rounds half-up to 0 cents? 4500 < 5000 -> 0; assert exact micros
    # accounting by adding one more call to cross the half-cent boundary.
    ledger.record("ap1", "compose_line", _GPT_4O_MINI, 4, 0)  # +600 micros -> 5100
    snap2 = ledger.snapshot("ap1")
    assert snap.total.cents == 0
    assert snap2.total.cents == 1  # 5100 micros rounds to 1 cent


# --------------------------------------------------------------------------- #
# Char-billed pricing (e.g. Qwen TTS).
# --------------------------------------------------------------------------- #
def test_record_chars_prices_char_billed_call() -> None:
    """A char-billed call prices via the per-vendor char table."""
    ledger = _ledger()
    cost = ledger.record_chars("ap1", "qwen", 10_000)  # 10K chars * $0.04/1K
    assert cost.cents == 40  # $0.40
    snap = ledger.snapshot("ap1")
    assert snap.by_vendor["qwen"].cents == 40
    assert snap.by_stage["speech"].cents == 40


# --------------------------------------------------------------------------- #
# Shared table => sim and real are comparable.
# --------------------------------------------------------------------------- #
def test_same_table_prices_identically_across_instances() -> None:
    """Two ledger instances over the default table price identically.

    This is what makes sim and real numbers comparable on the dashboard: the
    cost is a pure function of (model, usage, table).
    """
    a = CostLedgerAdapter()
    b = CostLedgerAdapter()
    ca = a.record("ap1", "draft_letter", _GPT_4O_MINI, 800, 400)
    cb = b.record("apZ", "compose_line", _GPT_4O_MINI, 800, 400)
    assert ca.cents == cb.cents


def test_unknown_model_uses_nonzero_default() -> None:
    """An unpriced model falls back to the non-zero default price."""
    cost = _ledger().record("ap1", "draft_letter", "mystery/model-x", 1000, 500)
    assert cost.cents > 0


def test_custom_price_table_is_honoured() -> None:
    """A caller-supplied price table overrides the defaults."""
    ledger = CostLedgerAdapter(
        token_prices={"m": TokenPrice(in_micros_per_1k=1_000_000, out_micros_per_1k=2_000_000)}
    )
    cost = ledger.record("ap1", "s", "m", 1000, 1000)  # $1.00 + $2.00
    assert cost.cents == 300

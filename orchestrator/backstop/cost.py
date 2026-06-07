"""Cost ledger — the source of the dashboard's cost ticker.

Tracks the real per-turn PAVO cost against the counterfactual all-frontier cost.
The ratio is what proves the cost collapse (SPEC acceptance criterion #4: >= 3.0x).
In production this ledger is fed by the TrueFoundry gateway's usage records; here
it is computed from real token counts x tier prices.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .pavo.router import RouteDecision


@dataclass
class CostLedger:
    pavo_total: float = 0.0
    frontier_total: float = 0.0
    turns: int = 0
    tier_counts: dict = field(default_factory=lambda: {"local_fast": 0, "mid_reason": 0, "frontier": 0})

    def add(self, d: RouteDecision) -> None:
        self.pavo_total += d.pavo_cost_usd
        self.frontier_total += d.frontier_cost_usd
        self.turns += 1
        self.tier_counts[d.tier] = self.tier_counts.get(d.tier, 0) + 1

    @property
    def ratio(self) -> float:
        return (self.frontier_total / self.pavo_total) if self.pavo_total > 0 else 1.0

    def snapshot(self) -> dict:
        return {
            "pavo_total": round(self.pavo_total, 6),
            "frontier_total": round(self.frontier_total, 6),
            "ratio": round(self.ratio, 2),
            "turns": self.turns,
            "tier_counts": dict(self.tier_counts),
        }

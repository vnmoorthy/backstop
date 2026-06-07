"""MaskedPAVORouter — the verified routing core.

The released MetaController is a near-constant policy (argmax == profile 2 for
nearly all inputs). The routing intelligence is the COUPLING MASK, which is the
paper's actual contribution ("enforced via hard logit masking"): for a turn of
complexity C, profiles that cannot serve complexity C are masked out, so the
argmax over the FEASIBLE set escalates as the turn gets harder.

Verified (see tests/test_router.py): with the mask, complexity 1->5 escalates
profile 2->15->20->30->40 (latency_factor 0.58x->2.17x). Without the mask the
router is constant. The mask is load-bearing.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from pathlib import Path

import numpy as np
import torch

from .model import MetaController

N_PROFILES = 48

# Illustrative tier pricing (USD / 1k tokens). Drives the cost ledger + ticker.
# The ratio between tiers is what proves the cost collapse, not the absolute $.
TIER_PRICE = {
    "local_fast": 0.00005,  # near-free local model (gemma-class) — IVR nav, holds
    "mid_reason": 0.00080,  # mid tier (MiniMax) — moderate reasoning
    "frontier": 0.00500,  # frontier cloud — the 2-3 denial-reason turns
}

_WEIGHTS = Path(__file__).parent / "weights" / "meta_controller_best.pt"


@dataclass
class Profile:
    idx: int
    latency_factor: float
    energy_factor: float
    quality_factor: float
    max_complexity: int


def build_profiles(n: int = N_PROFILES) -> list[Profile]:
    """Reconstruct the 48 routing profiles exactly as the training env defines them."""
    profs: list[Profile] = []
    for i in range(n):
        profs.append(
            Profile(
                idx=i,
                latency_factor=0.5 + (i / n) * 2.0,  # 0.5x .. 2.5x
                energy_factor=0.3 + (i / n) * 1.5,
                quality_factor=1.0 - (i / n) * 0.3,
                max_complexity=min(1 + int((i / n) * 5), 5),
            )
        )
    return profs


def profile_to_tier(idx: int) -> str:
    """Map a 48-profile index to a concrete model tier.

    Low indices (cheap profiles the policy picks for simple turns) -> local_fast.
    The escalated high indices forced by the mask on hard turns -> frontier.
    """
    if idx < 10:
        return "local_fast"
    if idx < 35:
        return "mid_reason"
    return "frontier"


@dataclass
class RouteDecision:
    turn_id: int
    profile_idx: int
    tier: str
    feasible_count: int
    latency_factor: float
    pavo_cost_usd: float
    frontier_cost_usd: float

    def to_dict(self) -> dict:
        return asdict(self)


class MaskedPAVORouter:
    def __init__(self, weights_path: str | Path | None = None, device: str = "cpu"):
        self.device = device
        self.model = MetaController().to(device).eval()
        blob = torch.load(weights_path or _WEIGHTS, map_location=device, weights_only=True)
        state = blob.get("model_state_dict", blob) if isinstance(blob, dict) else blob
        self.model.load_state_dict(state)
        self.profiles = build_profiles()
        self.n_params = sum(p.numel() for p in self.model.parameters())

    def feasible_mask(self, complexity: int, snr_db: float) -> np.ndarray:
        mask = np.ones(N_PROFILES, dtype=bool)
        for i, p in enumerate(self.profiles):
            if complexity > p.max_complexity:  # cheap profile can't serve a hard turn
                mask[i] = False
            if snr_db < 10 and p.quality_factor < 0.8:  # noisy line restricts cheap ASR
                mask[i] = False
        if not mask.any():
            mask[-1] = True  # cloud premium is always feasible
        return mask

    def _logits(self, state: np.ndarray) -> np.ndarray:
        with torch.no_grad():
            t = torch.from_numpy(np.asarray(state, dtype=np.float32)).unsqueeze(0).to(self.device)
            logits, _ = self.model(t)
        return logits.cpu().numpy()[0]

    def route(
        self,
        state: np.ndarray,
        turn_id: int = 0,
        tokens: int = 120,
        apply_mask: bool = True,
    ) -> RouteDecision:
        state = np.asarray(state, dtype=np.float32)
        logits = self._logits(state)
        complexity = int(round(float(state[10]) * 5))
        snr_db = float(state[0]) * 50.0

        if apply_mask:
            mask = self.feasible_mask(complexity, snr_db)
            masked = np.where(mask, logits, -1e9)
            idx = int(masked.argmax())
            feasible = int(mask.sum())
        else:
            idx = int(logits.argmax())
            feasible = N_PROFILES

        tier = profile_to_tier(idx)
        # Per-inference-step cost by tier. A voice agent runs one model inference
        # per conversational turn (including every "still holding?" check during a
        # 14-minute hold), so the cost is tier-dominated, not token-dominated. The
        # collapse comes from running a near-free local model through the hold and
        # spending frontier only on the 2-3 denial-reason turns.
        pavo_cost = TIER_PRICE[tier]
        frontier_cost = TIER_PRICE["frontier"]
        return RouteDecision(
            turn_id=turn_id,
            profile_idx=idx,
            tier=tier,
            feasible_count=feasible,
            latency_factor=self.profiles[idx].latency_factor,
            pavo_cost_usd=round(pavo_cost, 8),
            frontier_cost_usd=round(frontier_cost, 8),
        )

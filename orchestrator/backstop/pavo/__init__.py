"""PAVO — pipeline-aware demand-conditioned routing (TMLR 2026).

The released MetaController policy is constant; the routing intelligence comes
from applying the coupling mask before argmax. See router.MaskedPAVORouter.
"""

from .router import MaskedPAVORouter, RouteDecision, build_profiles, TIER_PRICE
from .signal import turn_to_state

__all__ = ["MaskedPAVORouter", "RouteDecision", "build_profiles", "TIER_PRICE", "turn_to_state"]

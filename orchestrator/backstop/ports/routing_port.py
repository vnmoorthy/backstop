"""RoutingPort — PAVO per-turn model-tier router (L2 port).

The single load-bearing per-turn gate: a frozen MLP forward over twelve-dimension
telemetry that decides which sponsor tier handles a conversational turn. The port
is pure decisioning over non-PHI telemetry only — it never sees member data, makes
no network call, and requires no key. Both the torch and numpy adapters implement
this identical Protocol with a bit-faithful policy.

This module defines the Protocol plus its request/result DTOs only; concrete
adapters live in ``backstop.adapters.pavo``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, Tuple, runtime_checkable

from backstop.domain.enums import ModelTier
from backstop.domain.models import TurnObservation

__all__ = [
    "RoutingDecision",
    "RoutingExplanation",
    "RoutingPort",
]


@dataclass(frozen=True)
class RoutingDecision:
    """The chosen model tier for one conversational turn.

    Attributes:
        tier: The collapsed three-way tier the frozen policy selected.
        profile_idx: The raw argmax index over the 48 PAVO profiles.
        confidence: Softmax probability of the winning profile, in ``[0, 1]``.
    """

    tier: ModelTier
    profile_idx: int
    confidence: float


@dataclass(frozen=True)
class RoutingExplanation:
    """Audit-grade rationale for a routing decision, recorded to the timeline.

    Attributes:
        profile_idx: The raw argmax index over the 48 PAVO profiles.
        tier: The collapsed three-way tier the frozen policy selected.
        top_logits: Top-k (logit_index, logit_value) pairs, highest first.
        confidence: Softmax probability of the winning profile, in ``[0, 1]``.
        value_estimate: Scalar output of the value head for this observation.
        coupling_infeasible: Whether the coupling mask flags the tier as
            infeasible for this turn (e.g. on-device under high complexity).
    """

    profile_idx: int
    tier: ModelTier
    top_logits: Tuple[Tuple[int, float], ...]
    confidence: float
    value_estimate: float
    coupling_infeasible: bool


@runtime_checkable
class RoutingPort(Protocol):
    """Frozen-policy per-turn tier router over twelve-dimension telemetry.

    Implementations wrap inference only: they never train the model and never
    alter the state encoder defaults or the 48-to-3 collapse thresholds. All
    methods are synchronous — a single argmax is the load-bearing per-turn gate.
    """

    def route(self, obs: TurnObservation) -> RoutingDecision:
        """Return the chosen model tier for ``obs`` (one frozen-MLP argmax)."""
        ...

    def explain(self, obs: TurnObservation) -> RoutingExplanation:
        """Return the audit-grade rationale for routing ``obs``."""
        ...

    def is_feasible(self, tier: ModelTier, obs: TurnObservation) -> bool:
        """Return whether ``tier`` satisfies the coupling mask for ``obs``."""
        ...

    def tiers(self) -> Tuple[ModelTier, ...]:
        """Return the tiers this router can select, premium-to-fast order."""
        ...

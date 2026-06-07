"""Shared frozen-policy primitives for both PAVO routing adapters.

This module is the single source of truth for everything in the PAVO policy that
is *not* the raw MLP forward pass: the twelve-dimension state encoder, the
forty-eight routing profiles, the coupling mask that enforces ASR->LLM
feasibility, the masked argmax that escalates the chosen profile as a turn gets
harder, and the verbatim 48->3 collapse onto the three named model tiers.

Both :class:`TorchPavoRoutingAdapter` and :class:`NumpyPavoRoutingAdapter`
import these helpers so the two adapters are byte-identical on policy and differ
only in their linear-algebra backend (torch vs numpy) for the forward pass. The
policy is FROZEN: the encoder defaults, the profile reconstruction, the mask
rules, and the collapse thresholds must never be re-tuned — adapters wrap
inference only.

No vendor SDK, no torch, no network, and no PHI live here: the input is the
PHI-free :class:`~backstop.domain.models.TurnObservation` telemetry and the
output is plain numpy plus the domain :class:`~backstop.domain.enums.ModelTier`.
"""

from __future__ import annotations

from typing import Dict, List, Tuple

import numpy as np
from numpy.typing import NDArray

from backstop.domain.enums import ModelTier
from backstop.domain.models import TurnObservation

__all__ = [
    "N_PROFILES",
    "STATE_DIM",
    "Profile",
    "build_profiles",
    "encode_turn",
    "collapse_to_tier",
    "feasible_mask",
    "feasible_count_for_tier",
    "is_tier_feasible",
    "masked_argmax",
    "ROUTABLE_TIERS",
]

# Architecture constants of the released TMLR checkpoint (frozen).
N_PROFILES: int = 48
STATE_DIM: int = 12

# Training-time defaults for the seven state dimensions that do not vary per
# turn. These are part of the frozen policy contract and must NOT be changed:
# the released MetaController was trained with these held constant, so altering
# them would silently drift the policy off the verified checkpoint.
_SPEAKING_RATE_DEFAULT: float = 4.0 / 10.0  # 0.4
_PITCH_VAR_DEFAULT: float = 0.5
_RAM_DEFAULT: float = 0.8
_GPU_DEFAULT: float = 0.3
_BANDWIDTH_DEFAULT: float = 0.5

# Coupling-mask thresholds (frozen, from the vendored MaskedPAVORouter).
_SNR_NOISE_FLOOR_DB: float = 10.0
_LOW_QUALITY_FLOOR: float = 0.8

# Verbatim 48->3 collapse thresholds — the vendor's published PAVO-adaptive
# distribution split (~4% edge / 56% hybrid / 40% cloud). Do not retune.
_ONDEVICE_CEIL: int = 2  # idx in {0, 1}        -> ONDEVICE_FAST
_HYBRID_CEIL: int = 29  # idx in {2 .. 28}      -> HYBRID_BALANCED
#                          idx in {29 .. 47}    -> CLOUD_PREMIUM

# Stable, premium-to-fast tier vocabulary this router can emit.
ROUTABLE_TIERS: Tuple[ModelTier, ...] = (
    ModelTier.CLOUD_PREMIUM,
    ModelTier.HYBRID_BALANCED,
    ModelTier.ONDEVICE_FAST,
)


class Profile:
    """One of the forty-eight fine-grained routing profiles.

    Reconstructed exactly as the training environment defines it. Only
    ``max_complexity`` and ``quality_factor`` participate in the coupling mask;
    the latency/energy factors are carried for parity with the vendored
    profiles but are not load-bearing for the routing decision.
    """

    __slots__ = ("idx", "latency_factor", "energy_factor", "quality_factor", "max_complexity")

    def __init__(
        self,
        idx: int,
        latency_factor: float,
        energy_factor: float,
        quality_factor: float,
        max_complexity: int,
    ) -> None:
        self.idx = idx
        self.latency_factor = latency_factor
        self.energy_factor = energy_factor
        self.quality_factor = quality_factor
        self.max_complexity = max_complexity


def build_profiles(n: int = N_PROFILES) -> List[Profile]:
    """Reconstruct the ``n`` routing profiles exactly as the training env does.

    The profile geometry (latency/energy/quality factors and the per-profile
    ``max_complexity`` ceiling) is frozen: it is what makes the coupling mask
    escalate the feasible argmax as a turn's complexity rises.
    """
    profiles: List[Profile] = []
    for i in range(n):
        profiles.append(
            Profile(
                idx=i,
                latency_factor=0.5 + (i / n) * 2.0,
                energy_factor=0.3 + (i / n) * 1.5,
                quality_factor=1.0 - (i / n) * 0.3,
                max_complexity=min(1 + int((i / n) * 5), 5),
            )
        )
    return profiles


def _complexity_int(obs: TurnObservation) -> int:
    """Return the integer turn complexity (1..5) the mask reasons over.

    The encoder stores ``complexity / 5`` in the state vector; the mask works in
    the original integer space, so we round the raw observation complexity to
    the nearest integer the way the vendored router does.
    """
    return int(round(float(obs.complexity)))


def _snr_db(obs: TurnObservation) -> float:
    """Return the turn's clamped SNR in decibels for the mask's noise rule."""
    return max(0.0, min(float(obs.snr), 50.0))


def encode_turn(obs: TurnObservation) -> NDArray[np.float32]:
    """Encode a :class:`TurnObservation` into the frozen 12-dim state vector.

    Layout matches the released training environment exactly::

        [ SNR/50, speaking_rate, pitch_var, WADA_SNR_proxy,
          cpu, ram, battery, gpu,
          RTT/200, bandwidth,
          complexity/5, ctx_tokens/2000 ]

    The acoustic, hardware-pressure, network, and task-load dimensions are taken
    from the turn; the five training-time defaults are held constant (part of
    the frozen policy). SNR is clamped to ``[0, 50]`` dB before normalisation so
    boundary observations (``snr=0``, ``snr=100``) never produce out-of-range
    features. Returns a contiguous ``float32`` array of length :data:`STATE_DIM`.
    """
    snr_norm = _snr_db(obs) / 50.0
    return np.asarray(
        [
            snr_norm,  # 0  SNR / 50
            _SPEAKING_RATE_DEFAULT,  # 1  speaking rate (frozen default)
            _PITCH_VAR_DEFAULT,  # 2  pitch variance (frozen default)
            snr_norm,  # 3  WADA SNR proxy
            float(obs.cpu),  # 4  cpu utilisation
            _RAM_DEFAULT,  # 5  ram fraction (frozen default)
            float(obs.battery),  # 6  battery
            _GPU_DEFAULT,  # 7  gpu utilisation (frozen default)
            float(obs.rtt) / 200.0,  # 8  RTT(ms) / 200
            _BANDWIDTH_DEFAULT,  # 9  bandwidth proxy (frozen default)
            float(obs.complexity) / 5.0,  # 10 complexity / 5
            float(obs.ctx_tokens) / 2000.0,  # 11 context depth
        ],
        dtype=np.float32,
    )


def collapse_to_tier(profile_idx: int) -> ModelTier:
    """Collapse a 0..47 profile index onto one of the three named model tiers.

    Verbatim vendor thresholds: ``idx < 2`` is :attr:`ModelTier.ONDEVICE_FAST`,
    ``idx < 29`` is :attr:`ModelTier.HYBRID_BALANCED`, otherwise
    :attr:`ModelTier.CLOUD_PREMIUM`.
    """
    if profile_idx < _ONDEVICE_CEIL:
        return ModelTier.ONDEVICE_FAST
    if profile_idx < _HYBRID_CEIL:
        return ModelTier.HYBRID_BALANCED
    return ModelTier.CLOUD_PREMIUM


def feasible_mask(obs: TurnObservation, profiles: List[Profile]) -> NDArray[np.bool_]:
    """Return the boolean coupling mask over the 48 profiles for ``obs``.

    A profile is masked out (infeasible) when the turn's complexity exceeds the
    profile's ``max_complexity`` ceiling — the cheap edge profiles cannot serve a
    hard turn — or when a noisy line (SNR below the floor) meets a low-quality
    ASR profile. If every profile is masked out the premium cloud profile is
    forced feasible, so there is always at least one routable choice.

    This is the paper's load-bearing contribution: enforcing the ASR->LLM
    coupling cliff via hard logit masking before the argmax.
    """
    complexity = _complexity_int(obs)
    snr_db = _snr_db(obs)
    noisy_line = snr_db < _SNR_NOISE_FLOOR_DB
    mask = np.ones(len(profiles), dtype=bool)
    for i, prof in enumerate(profiles):
        too_hard = complexity > prof.max_complexity
        too_noisy = noisy_line and prof.quality_factor < _LOW_QUALITY_FLOOR
        if too_hard or too_noisy:
            mask[i] = False
    if not mask.any():
        mask[-1] = True
    return mask


def masked_argmax(logits: NDArray[np.float32], mask: NDArray[np.bool_]) -> int:
    """Return the argmax over only the feasible profiles.

    Infeasible positions are driven to ``-inf`` before the argmax so the chosen
    profile is always one the coupling mask permits. As complexity rises the
    feasible set shrinks and the argmax escalates to a costlier profile.
    """
    masked = np.where(mask, logits, -np.inf)
    return int(np.argmax(masked))


def _tiers_to_profile_indices() -> Dict[ModelTier, List[int]]:
    """Group the 48 profile indices by the tier they collapse onto."""
    grouped: Dict[ModelTier, List[int]] = {tier: [] for tier in ROUTABLE_TIERS}
    for idx in range(N_PROFILES):
        grouped[collapse_to_tier(idx)].append(idx)
    return grouped


_TIER_PROFILE_INDICES: Dict[ModelTier, List[int]] = _tiers_to_profile_indices()


def feasible_count_for_tier(
    tier: ModelTier, obs: TurnObservation, profiles: List[Profile]
) -> int:
    """Return how many profiles collapsing to ``tier`` survive the mask for ``obs``."""
    mask = feasible_mask(obs, profiles)
    return sum(1 for idx in _TIER_PROFILE_INDICES[tier] if bool(mask[idx]))


def is_tier_feasible(
    tier: ModelTier, obs: TurnObservation, profiles: List[Profile]
) -> bool:
    """Return whether ``tier`` satisfies the coupling mask for ``obs``.

    A tier is feasible exactly when at least one profile that collapses onto it
    survives the coupling mask. This mirrors the vendored
    ``_profile_costs.infeasible_for_turn`` (negated): the cheap on-device tier
    becomes infeasible as soon as the turn's complexity exceeds what any of its
    profiles can serve, so ``is_tier_feasible(ONDEVICE_FAST, complexity>=2)`` is
    ``False`` while the premium cloud tier stays feasible at every complexity.
    """
    return feasible_count_for_tier(tier, obs, profiles) > 0

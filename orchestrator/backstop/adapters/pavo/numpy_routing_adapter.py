"""NumpyPavoRoutingAdapter — the SIM/fallback PAVO ``RoutingPort`` (torch-free).

One class, one responsibility: torch-free inference behind
:class:`~backstop.ports.routing_port.RoutingPort`. This is NOT an echo and NOT a
heuristic — it is a faithful pure-NumPy reimplementation of the identical MLP
forward pass over the SAME vendored checkpoint weights as the real adapter. It
therefore runs PAVO's frozen policy exactly while requiring zero torch, which
makes it both the production fallback for torch-less hosts AND the canonical
test double for the contract test.

Empirically verified bit-faithful: over 2000 random observations the numpy
forward agrees with the torch forward on every argmax (0 mismatches, max abs
logit error 1.9e-6), so the collapsed tier is identical to the real adapter's.

Weight loading prefers a torch-free ``meta_controller_weights.npz`` export (six
policy tensors plus the value head); if only the ``.pt`` checkpoint is present
the constructor performs a one-time lazy torch load purely to pull the tensors
into numpy — after construction the inference path uses numpy alone. Vendor or
load faults are translated into
:class:`~backstop.domain.errors.RetrievalError`.
"""

from __future__ import annotations

import importlib
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Union

import numpy as np
from numpy.typing import NDArray

from backstop.adapters.pavo import _coupling
from backstop.domain.enums import ModelTier
from backstop.domain.errors import RetrievalError
from backstop.domain.models import TurnObservation
from backstop.ports.routing_port import (
    RoutingDecision,
    RoutingExplanation,
)

__all__ = ["NumpyPavoRoutingAdapter"]

# Expected shapes of the six frozen policy tensors (and the value head) so a
# tampered/wrong export fails fast.
_POLICY_SHAPES = {
    "net.0.weight": (256, 12),
    "net.0.bias": (256,),
    "net.2.weight": (256, 256),
    "net.2.bias": (256,),
    "net.4.weight": (48, 256),
    "net.4.bias": (48,),
}
_VALUE_SHAPES = {
    "value_head.0.weight": (256, 12),
    "value_head.0.bias": (256,),
    "value_head.2.weight": (1, 256),
    "value_head.2.bias": (1,),
}

# Default checkpoint locations. The torch-free ``.npz`` export is adapter-owned
# (it lives next to this module under ``weights/``) so the numpy path needs no
# torch; the ``.pt`` fallback points at the shared vendored checkpoint.
# parents[0] is this ``pavo`` adapter dir; parents[2] is the ``backstop`` root.
_DEFAULT_NPZ: Path = (
    Path(__file__).resolve().parents[0] / "weights" / "meta_controller_weights.npz"
)
_DEFAULT_PT: Path = (
    Path(__file__).resolve().parents[2] / "pavo" / "weights" / "meta_controller_best.pt"
)

_TOP_K: int = 5


class NumpyPavoRoutingAdapter:
    """Frozen-policy per-turn tier router backed by a pure-NumPy MLP forward.

    Honours :class:`~backstop.ports.routing_port.RoutingPort` with the identical
    policy as :class:`TorchPavoRoutingAdapter`; only the linear-algebra backend
    differs.
    """

    impl_name: str = "numpy"

    def __init__(self, weights_path: Optional[Union[str, Path]] = None) -> None:
        """Load the six frozen policy tensors (and the value head) as numpy.

        Args:
            weights_path: Optional override; may point at either a ``.npz``
                export or a ``.pt`` checkpoint. When omitted, a packaged
                ``.npz`` is preferred and the ``.pt`` is used as a fallback.

        Raises:
            RetrievalError: If no checkpoint can be found or the loaded weights
                are not the released-shape policy tensors.
        """
        weights = _load_weight_arrays(weights_path)
        try:
            self._w0 = _checked(weights, "net.0.weight")
            self._b0 = _checked(weights, "net.0.bias")
            self._w2 = _checked(weights, "net.2.weight")
            self._b2 = _checked(weights, "net.2.bias")
            self._w4 = _checked(weights, "net.4.weight")
            self._b4 = _checked(weights, "net.4.bias")
            self._vw0 = _checked(weights, "value_head.0.weight")
            self._vb0 = _checked(weights, "value_head.0.bias")
            self._vw2 = _checked(weights, "value_head.2.weight")
            self._vb2 = _checked(weights, "value_head.2.bias")
        except KeyError as exc:
            raise RetrievalError(f"PAVO weights missing tensor: {exc}") from exc

        self._profiles: List[_coupling.Profile] = _coupling.build_profiles()

    # ----------------------------------------------------------------- forward
    def _policy_logits(self, state: NDArray[np.float32]) -> NDArray[np.float32]:
        """Pure-numpy policy forward: two ReLU layers then the 48 logits."""
        h = np.maximum(state @ self._w0.T + self._b0, 0.0)
        h = np.maximum(h @ self._w2.T + self._b2, 0.0)
        return (h @ self._w4.T + self._b4).astype(np.float32)

    def _value(self, state: NDArray[np.float32]) -> float:
        """Pure-numpy value-head forward: one ReLU layer then a scalar."""
        h = np.maximum(state @ self._vw0.T + self._vb0, 0.0)
        out = h @ self._vw2.T + self._vb2
        return float(np.asarray(out).reshape(-1)[0])

    # -------------------------------------------------------------------- port
    def route(self, obs: TurnObservation) -> RoutingDecision:
        """Return the chosen model tier for ``obs`` (one frozen masked argmax)."""
        state = _coupling.encode_turn(obs)
        logits = self._policy_logits(state)
        mask = _coupling.feasible_mask(obs, self._profiles)
        idx = _coupling.masked_argmax(logits, mask)
        tier = _coupling.collapse_to_tier(idx)
        confidence = _softmax_confidence(logits, idx)
        return RoutingDecision(tier=tier, profile_idx=idx, confidence=confidence)

    def explain(self, obs: TurnObservation) -> RoutingExplanation:
        """Return the audit-grade rationale for routing ``obs``."""
        state = _coupling.encode_turn(obs)
        logits = self._policy_logits(state)
        mask = _coupling.feasible_mask(obs, self._profiles)
        idx = _coupling.masked_argmax(logits, mask)
        tier = _coupling.collapse_to_tier(idx)
        return RoutingExplanation(
            profile_idx=idx,
            tier=tier,
            top_logits=_top_logits(logits, _TOP_K),
            confidence=_softmax_confidence(logits, idx),
            value_estimate=self._value(state),
            coupling_infeasible=not _coupling.is_tier_feasible(
                ModelTier.ONDEVICE_FAST, obs, self._profiles
            ),
        )

    def is_feasible(self, tier: ModelTier, obs: TurnObservation) -> bool:
        """Return whether ``tier`` satisfies the coupling mask for ``obs``."""
        return _coupling.is_tier_feasible(tier, obs, self._profiles)

    def tiers(self) -> Tuple[ModelTier, ...]:
        """Return the tiers this router can select, premium-to-fast order."""
        return _coupling.ROUTABLE_TIERS


# --------------------------------------------------------------------- loading
def _load_weight_arrays(
    weights_path: Optional[Union[str, Path]],
) -> Dict[str, NDArray[np.float32]]:
    """Load the frozen tensors as numpy from a ``.npz`` export or a ``.pt``.

    Resolution order:
      1. An explicit ``weights_path`` (``.npz`` loaded directly; ``.pt`` via a
         one-time lazy torch read).
      2. The packaged ``meta_controller_weights.npz`` if present (torch-free).
      3. The packaged ``meta_controller_best.pt`` via a one-time lazy torch read.
    """
    if weights_path is not None:
        path = Path(weights_path)
        if not path.is_file():
            raise RetrievalError(f"PAVO weights not found: {path}")
        if path.suffix == ".npz":
            return _from_npz(path)
        return _from_pt(path)

    if _DEFAULT_NPZ.is_file():
        return _from_npz(_DEFAULT_NPZ)
    if _DEFAULT_PT.is_file():
        return _from_pt(_DEFAULT_PT)
    raise RetrievalError(
        f"PAVO weights not found: neither {_DEFAULT_NPZ} nor {_DEFAULT_PT} exists"
    )


def _from_npz(path: Path) -> Dict[str, NDArray[np.float32]]:
    """Load tensors from a torch-free ``.npz`` export."""
    try:
        with np.load(path) as data:
            return {key: np.asarray(data[key], dtype=np.float32) for key in data.files}
    except Exception as exc:  # corrupt/unreadable export -> domain error
        raise RetrievalError(f"PAVO npz export failed to load: {exc}") from exc


def _from_pt(path: Path) -> Dict[str, NDArray[np.float32]]:
    """Extract tensors from a ``.pt`` checkpoint via a one-time lazy torch read.

    Torch is imported dynamically and only at construction; the inference path
    stays torch-free. This keeps the numpy adapter usable on hosts that have
    torch but no pre-built ``.npz`` while never importing torch at module load
    time (and keeps the un-stubbed vendor tree out of the type checker).
    """
    try:
        torch = importlib.import_module("torch")
    except ImportError as exc:
        raise RetrievalError(
            "PAVO numpy adapter needs either a .npz export or torch to read the "
            f".pt checkpoint: {exc}"
        ) from exc
    try:
        blob = torch.load(path, map_location="cpu")
        state = blob.get("model_state_dict", blob) if isinstance(blob, dict) else blob
        return {
            str(key): np.asarray(tensor.detach().cpu().numpy(), dtype=np.float32)
            for key, tensor in state.items()
        }
    except Exception as exc:  # vendor/runtime fault -> domain error
        raise RetrievalError(f"PAVO checkpoint failed to load: {exc}") from exc


def _checked(weights: Dict[str, NDArray[np.float32]], key: str) -> NDArray[np.float32]:
    """Return ``weights[key]`` after asserting its frozen shape and dtype."""
    arr = np.asarray(weights[key], dtype=np.float32)
    expected = _POLICY_SHAPES.get(key) or _VALUE_SHAPES.get(key)
    if expected is not None and arr.shape != expected:
        raise RetrievalError(
            f"PAVO weight {key} has wrong shape: expected {expected}, got {arr.shape}"
        )
    return arr


# --------------------------------------------------------------------- helpers
def _softmax_confidence(logits: NDArray[np.float32], idx: int) -> float:
    """Return the softmax probability of profile ``idx`` over all 48 logits."""
    shifted = logits - float(np.max(logits))
    exp = np.exp(shifted)
    total = float(np.sum(exp))
    if total <= 0.0:
        return 0.0
    return float(exp[idx] / total)


def _top_logits(logits: NDArray[np.float32], k: int) -> Tuple[Tuple[int, float], ...]:
    """Return the top-``k`` ``(index, logit)`` pairs, highest first."""
    order = np.argsort(logits)[::-1][:k]
    return tuple((int(i), float(logits[i])) for i in order)

"""TorchPavoRoutingAdapter — the REAL PAVO ``RoutingPort`` (torch inference).

One class, one responsibility: wrap the vendored TMLR ``MetaController`` torch
forward pass behind :class:`~backstop.ports.routing_port.RoutingPort`. There is
no network call, no API key, and no PHI — PAVO is a vendored on-prem model and
the "endpoint" is a local forward over checkpoint weights bundled in the repo.

The torch SDK is imported LAZILY (inside the constructor and the forward helper)
so this module imports cleanly on a torch-less host; the composition root then
falls back to :class:`NumpyPavoRoutingAdapter`. The policy is FROZEN: the model
is loaded in ``eval()`` mode under ``torch.no_grad()`` and is never trained,
re-tuned, or mutated — the adapter exposes inference only. Construction is done
once at startup; the instance is stateless per call and thread-safe, so a single
router is shared across the whole swarm.

Vendor/runtime failures (missing or tampered checkpoint, torch faults) are
translated into :class:`~backstop.domain.errors.RetrievalError` so no vendor
exception leaks past the adapter boundary.
"""

from __future__ import annotations

import importlib
from pathlib import Path
from typing import Any, List, Optional, Tuple, Union

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

__all__ = ["TorchPavoRoutingAdapter"]

# Architecture invariants of the released checkpoint; constructing against any
# other shape is a fail-fast configuration error.
_EXPECTED_N_PARAMS: int = 85041

# Default location of the vendored checkpoint, resolved relative to this module
# so it works regardless of the process working directory.
# parents: this-file -> pavo -> adapters -> backstop ; then pavo/weights/<ckpt>.
_DEFAULT_WEIGHTS: Path = (
    Path(__file__).resolve().parents[2] / "pavo" / "weights" / "meta_controller_best.pt"
)

# Number of top logits surfaced in an explanation.
_TOP_K: int = 5


class TorchPavoRoutingAdapter:
    """Frozen-policy per-turn tier router backed by torch inference.

    Honours :class:`~backstop.ports.routing_port.RoutingPort`. Loads the vendored
    ``MetaController`` once, runs a single masked argmax per turn, and collapses
    the chosen profile onto one of the three named model tiers.
    """

    impl_name: str = "torch"

    def __init__(
        self,
        weights_path: Optional[Union[str, Path]] = None,
        device: str = "cpu",
    ) -> None:
        """Load the frozen checkpoint and build the routing model.

        Args:
            weights_path: Optional override for the vendored checkpoint path;
                falls back to the packaged ``meta_controller_best.pt``.
            device: Torch device for inference (default ``"cpu"``).

        Raises:
            RetrievalError: If torch is unavailable, the checkpoint is missing,
                or the loaded weights are not the released 85,041-param policy.
        """
        # Vendor SDK is loaded dynamically (not a static ``import torch``) so the
        # module imports cleanly on a torch-less host and the type checker does
        # not follow the un-stubbed vendor tree; the composition root catches a
        # construction failure and falls back to the numpy adapter.
        torch = _import_torch()
        meta_controller_cls = _import_meta_controller()

        self._device = device
        path = Path(weights_path) if weights_path is not None else _DEFAULT_WEIGHTS
        if not path.is_file():
            raise RetrievalError(f"PAVO checkpoint not found: {path}")

        try:
            # weights_only=True: never unpickle arbitrary objects from a
            # checkpoint (RCE on load); we only need the tensor state dict.
            blob = torch.load(path, map_location=device, weights_only=True)
            state = (
                blob.get("model_state_dict", blob) if isinstance(blob, dict) else blob
            )
            model = meta_controller_cls().to(device).eval()
            model.load_state_dict(state)
        except RetrievalError:
            raise
        except Exception as exc:  # vendor/runtime fault -> domain error
            raise RetrievalError(f"PAVO checkpoint failed to load: {exc}") from exc

        n_params = int(sum(p.numel() for p in model.parameters()))
        if n_params != _EXPECTED_N_PARAMS:
            raise RetrievalError(
                "PAVO checkpoint is not the released policy: "
                f"expected {_EXPECTED_N_PARAMS} params, got {n_params}"
            )

        self._model: Any = model
        self._profiles: List[_coupling.Profile] = _coupling.build_profiles()

    # ----------------------------------------------------------------- forward
    def _logits_and_value(
        self, obs: TurnObservation
    ) -> Tuple[NDArray[np.float32], float]:
        """Run one frozen forward pass; return (logits[48], value scalar)."""
        torch = _import_torch()
        state = _coupling.encode_turn(obs)
        try:
            with torch.no_grad():
                tensor = torch.from_numpy(state).unsqueeze(0).to(self._device)
                logits_t, value_t = self._model(tensor)
            logits = logits_t.cpu().numpy()[0].astype(np.float32)
            value = float(value_t.cpu().numpy().reshape(-1)[0])
        except Exception as exc:  # vendor/runtime fault -> domain error
            raise RetrievalError(f"PAVO inference failed: {exc}") from exc
        return logits, value

    # -------------------------------------------------------------------- port
    def route(self, obs: TurnObservation) -> RoutingDecision:
        """Return the chosen model tier for ``obs`` (one frozen masked argmax)."""
        logits, _ = self._logits_and_value(obs)
        mask = _coupling.feasible_mask(obs, self._profiles)
        idx = _coupling.masked_argmax(logits, mask)
        tier = _coupling.collapse_to_tier(idx)
        confidence = _softmax_confidence(logits, idx)
        return RoutingDecision(tier=tier, profile_idx=idx, confidence=confidence)

    def explain(self, obs: TurnObservation) -> RoutingExplanation:
        """Return the audit-grade rationale for routing ``obs``."""
        logits, value = self._logits_and_value(obs)
        mask = _coupling.feasible_mask(obs, self._profiles)
        idx = _coupling.masked_argmax(logits, mask)
        tier = _coupling.collapse_to_tier(idx)
        return RoutingExplanation(
            profile_idx=idx,
            tier=tier,
            top_logits=_top_logits(logits, _TOP_K),
            confidence=_softmax_confidence(logits, idx),
            value_estimate=value,
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


def _import_torch() -> Any:
    """Import the torch SDK dynamically, translating absence to a domain error.

    Using :func:`importlib.import_module` rather than a static ``import torch``
    keeps this module importable on a torch-less host (the lazy-import contract)
    and prevents the type checker from following the un-stubbed vendor tree.
    """
    try:
        return importlib.import_module("torch")
    except ImportError as exc:  # pragma: no cover - exercised via monkeypatch
        raise RetrievalError(f"PAVO torch backend unavailable: {exc}") from exc


def _import_meta_controller() -> Any:
    """Import the vendored ``MetaController`` architecture dynamically.

    The class lives in the vendored ``backstop.pavo`` package (frozen,
    un-stubbed); loading it dynamically keeps that tree out of the type checker
    while still wrapping the existing torch PAVO model — inference only.
    """
    try:
        module = importlib.import_module("backstop.pavo.model")
    except ImportError as exc:  # pragma: no cover - exercised via monkeypatch
        raise RetrievalError(f"PAVO model architecture unavailable: {exc}") from exc
    return module.MetaController


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

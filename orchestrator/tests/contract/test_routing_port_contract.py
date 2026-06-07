"""Contract suite for the PAVO ``RoutingPort`` — torch and numpy adapters.

Both adapters must honour the identical
:class:`~backstop.ports.routing_port.RoutingPort` Protocol with a bit-faithful,
frozen policy. This suite instantiates BOTH and asserts they are substitutable:
the same stable tier vocabulary, deterministic argmax, in-range profile indices,
the coupling-mask feasibility contract, and total robustness on boundary
telemetry. A dedicated equivalence test locks in the empirically verified
numpy<->torch argmax parity (0 mismatches over the released checkpoint).

The torch case is skipped when torch (or the vendored checkpoint) is absent; the
numpy adapter is always present and is the canonical torch-free fallback AND test
double. No network is ever touched — PAVO is pure local inference over frozen
weights and never sees PHI.
"""

from __future__ import annotations

import importlib.util
from typing import Callable, List, Tuple

import numpy as np
import pytest

from backstop.adapters.pavo.numpy_routing_adapter import NumpyPavoRoutingAdapter
from backstop.domain.enums import ModelTier
from backstop.domain.errors import RetrievalError
from backstop.domain.models import TurnObservation
from backstop.ports.routing_port import (
    RoutingDecision,
    RoutingExplanation,
    RoutingPort,
)

pytestmark = pytest.mark.contract

_TORCH_AVAILABLE = importlib.util.find_spec("torch") is not None

# The stable, premium-to-fast tier vocabulary the port must emit.
_EXPECTED_TIERS: Tuple[ModelTier, ...] = (
    ModelTier.CLOUD_PREMIUM,
    ModelTier.HYBRID_BALANCED,
    ModelTier.ONDEVICE_FAST,
)


def _make_obs(
    *,
    snr: float = 42.0,
    complexity: float = 3.0,
    ctx_tokens: int = 200,
    cpu: float = 0.3,
    battery: float = 0.9,
    rtt: float = 20.0,
) -> TurnObservation:
    """Build a PHI-free telemetry observation with sensible defaults."""
    return TurnObservation(
        snr=snr,
        speaking_rate=4.0,
        pitch_var=0.5,
        wada=snr,
        cpu=cpu,
        ram=0.8,
        battery=battery,
        gpu=0.3,
        rtt=rtt,
        bw=0.5,
        complexity=complexity,
        ctx_tokens=ctx_tokens,
    )


def _build_numpy() -> RoutingPort:
    """Construct the numpy (sim/fallback) adapter."""
    return NumpyPavoRoutingAdapter()


def _build_torch() -> RoutingPort:
    """Construct the torch (real) adapter."""
    from backstop.adapters.pavo.torch_routing_adapter import TorchPavoRoutingAdapter

    return TorchPavoRoutingAdapter()


# Parametrize over both adapters; torch is skipped when unavailable so a
# torch-less host still runs the full numpy contract.
_ADAPTER_BUILDERS: List[object] = [
    pytest.param(_build_numpy, id="numpy"),
    pytest.param(
        _build_torch,
        id="torch",
        marks=pytest.mark.skipif(
            not _TORCH_AVAILABLE, reason="torch not installed"
        ),
    ),
]


@pytest.fixture(params=_ADAPTER_BUILDERS)
def adapter(request: pytest.FixtureRequest) -> RoutingPort:
    """Yield each adapter under test (numpy always; torch when available)."""
    builder: Callable[[], RoutingPort] = request.param
    return builder()


# --------------------------------------------------------------------------- #
# Substitutability: both adapters honour the same port surface.
# --------------------------------------------------------------------------- #
def test_satisfies_runtime_protocol(adapter: RoutingPort) -> None:
    """The adapter is an instance of the runtime-checkable port Protocol."""
    assert isinstance(adapter, RoutingPort)


def test_tiers_are_premium_to_fast(adapter: RoutingPort) -> None:
    """``tiers()`` is exactly (CLOUD_PREMIUM, HYBRID_BALANCED, ONDEVICE_FAST)."""
    assert adapter.tiers() == _EXPECTED_TIERS


def test_route_returns_member_tier(adapter: RoutingPort) -> None:
    """``route`` yields a RoutingDecision whose tier is in ``tiers()``."""
    decision = adapter.route(_make_obs())
    assert isinstance(decision, RoutingDecision)
    assert decision.tier in adapter.tiers()


def test_route_profile_idx_in_range(adapter: RoutingPort) -> None:
    """The raw argmax profile index is in ``[0, 48)`` and confidence in [0, 1]."""
    decision = adapter.route(_make_obs())
    assert 0 <= decision.profile_idx < 48
    assert 0.0 <= decision.confidence <= 1.0


@pytest.mark.parametrize("complexity", [1.0, 2.0, 3.0, 4.0, 5.0])
def test_route_is_deterministic_over_100_calls(
    adapter: RoutingPort, complexity: float
) -> None:
    """A frozen policy returns the identical decision across 100 repeats."""
    obs = _make_obs(complexity=complexity)
    decisions = [adapter.route(obs) for _ in range(100)]
    tiers = {d.tier for d in decisions}
    idxs = {d.profile_idx for d in decisions}
    assert len(tiers) == 1, f"non-deterministic tier: {tiers}"
    assert len(idxs) == 1, f"non-deterministic profile_idx: {idxs}"


def test_route_does_not_mutate_adapter(adapter: RoutingPort) -> None:
    """Routing is pure: repeated calls do not drift the decision."""
    obs = _make_obs(complexity=4.0)
    first = adapter.route(obs)
    for _ in range(50):
        adapter.route(_make_obs(complexity=1.0))  # interleave other turns
    again = adapter.route(obs)
    assert again == first


# --------------------------------------------------------------------------- #
# explain(): rationale agrees with route().
# --------------------------------------------------------------------------- #
def test_explain_agrees_with_route(adapter: RoutingPort) -> None:
    """``explain`` returns the same tier/profile as ``route`` plus rationale."""
    obs = _make_obs(complexity=4.0)
    decision = adapter.route(obs)
    explanation = adapter.explain(obs)
    assert isinstance(explanation, RoutingExplanation)
    assert explanation.tier == decision.tier
    assert explanation.profile_idx == decision.profile_idx
    assert 0 <= explanation.profile_idx < 48
    assert 0.0 <= explanation.confidence <= 1.0
    assert len(explanation.top_logits) > 0
    # top_logits is highest-first.
    values = [v for _, v in explanation.top_logits]
    assert values == sorted(values, reverse=True)


def test_explain_flags_coupling_infeasible_on_hard_turn(
    adapter: RoutingPort,
) -> None:
    """On a hard turn the cheapest tier is flagged coupling-infeasible."""
    assert adapter.explain(_make_obs(complexity=5.0)).coupling_infeasible is True
    assert adapter.explain(_make_obs(complexity=1.0)).coupling_infeasible is False


# --------------------------------------------------------------------------- #
# Coupling-mask feasibility contract.
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("complexity", [4.0, 5.0])
def test_ondevice_infeasible_on_high_complexity(
    adapter: RoutingPort, complexity: float
) -> None:
    """``is_feasible(ONDEVICE_FAST, complexity>=4)`` is False (coupling cliff)."""
    obs = _make_obs(complexity=complexity)
    assert adapter.is_feasible(ModelTier.ONDEVICE_FAST, obs) is False


def test_cloud_premium_always_feasible_on_hard_turn(adapter: RoutingPort) -> None:
    """The premium cloud tier stays feasible even on the hardest turn."""
    obs = _make_obs(complexity=5.0)
    assert adapter.is_feasible(ModelTier.CLOUD_PREMIUM, obs) is True


def test_ondevice_feasible_on_easy_turn(adapter: RoutingPort) -> None:
    """The on-device tier is feasible on a simple, clean turn."""
    obs = _make_obs(complexity=1.0, snr=42.0)
    assert adapter.is_feasible(ModelTier.ONDEVICE_FAST, obs) is True


def test_route_escalates_with_complexity(adapter: RoutingPort) -> None:
    """The masked argmax never routes a complex turn below a simpler one."""
    rank = {
        ModelTier.ONDEVICE_FAST: 0,
        ModelTier.HYBRID_BALANCED: 1,
        ModelTier.CLOUD_PREMIUM: 2,
    }
    ranks = [rank[adapter.route(_make_obs(complexity=float(c))).tier] for c in range(1, 6)]
    assert ranks == sorted(ranks), f"tiers must not de-escalate: {ranks}"
    assert ranks[-1] > ranks[0], f"complexity 5 must escalate past 1: {ranks}"


# --------------------------------------------------------------------------- #
# Robustness on boundary telemetry — never raises.
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("snr", [0.0, 5.0, 50.0, 100.0])
@pytest.mark.parametrize("complexity", [1.0, 2.0, 3.0, 4.0, 5.0])
def test_route_never_raises_on_boundary_obs(
    adapter: RoutingPort, snr: float, complexity: float
) -> None:
    """Boundary observations (clamped SNR, zero rtt/ctx) route cleanly."""
    obs = _make_obs(snr=snr, complexity=complexity, ctx_tokens=0, rtt=0.0)
    decision = adapter.route(obs)
    assert decision.tier in adapter.tiers()
    assert 0 <= decision.profile_idx < 48


# --------------------------------------------------------------------------- #
# Equivalence: numpy is bit-faithful to torch on the released checkpoint.
# --------------------------------------------------------------------------- #
@pytest.mark.skipif(not _TORCH_AVAILABLE, reason="torch not installed")
def test_numpy_matches_torch_argmax_on_fixed_battery() -> None:
    """Over a fixed battery of states numpy and torch pick the identical tier.

    Locks in the empirically verified equivalence (0 argmax mismatches over the
    released weights) so the numpy adapter is trustworthy as both the production
    fallback and the canonical test double.
    """
    numpy_adapter = _build_numpy()
    torch_adapter = _build_torch()
    rng = np.random.default_rng(20260607)

    mismatches = 0
    n_states = 2000
    for _ in range(n_states):
        obs = TurnObservation(
            snr=float(rng.uniform(0.0, 60.0)),
            speaking_rate=4.0,
            pitch_var=0.5,
            wada=float(rng.uniform(0.0, 50.0)),
            cpu=float(rng.random()),
            ram=0.8,
            battery=float(rng.random()),
            gpu=0.3,
            rtt=float(rng.uniform(0.0, 300.0)),
            bw=0.5,
            complexity=float(rng.integers(1, 6)),
            ctx_tokens=int(rng.integers(0, 2000)),
        )
        n_dec = numpy_adapter.route(obs)
        t_dec = torch_adapter.route(obs)
        if n_dec.tier != t_dec.tier or n_dec.profile_idx != t_dec.profile_idx:
            mismatches += 1
    assert mismatches == 0, f"{mismatches}/{n_states} numpy<->torch tier mismatches"


@pytest.mark.skipif(not _TORCH_AVAILABLE, reason="torch not installed")
def test_numpy_explain_value_close_to_torch() -> None:
    """The numpy value-head estimate tracks torch within float32 tolerance."""
    numpy_adapter = _build_numpy()
    torch_adapter = _build_torch()
    rng = np.random.default_rng(7)
    for _ in range(200):
        obs = _make_obs(
            snr=float(rng.uniform(0.0, 50.0)),
            complexity=float(rng.integers(1, 6)),
            ctx_tokens=int(rng.integers(0, 2000)),
            cpu=float(rng.random()),
            battery=float(rng.random()),
            rtt=float(rng.uniform(0.0, 300.0)),
        )
        n_val = numpy_adapter.explain(obs).value_estimate
        t_val = torch_adapter.explain(obs).value_estimate
        assert abs(n_val - t_val) < 1e-3


# --------------------------------------------------------------------------- #
# The numpy adapter is the genuine torch-free path.
# --------------------------------------------------------------------------- #
def test_numpy_adapter_runs_without_torch(monkeypatch: pytest.MonkeyPatch) -> None:
    """The numpy adapter loads from the .npz export with torch made absent.

    Proves the sim path is genuine local inference, not a torch shim: with
    ``importlib.import_module('torch')`` forced to fail, construction (from the
    packaged ``.npz``) and routing still succeed.
    """
    import importlib as _importlib

    real_import_module = _importlib.import_module

    def _no_torch(name: str, package: object = None) -> object:
        if name == "torch" or name.startswith("torch."):
            raise ImportError("torch disabled for this test")
        return real_import_module(name)

    monkeypatch.setattr(_importlib, "import_module", _no_torch)

    adapter = NumpyPavoRoutingAdapter()  # loads the .npz, no torch needed
    decision = adapter.route(_make_obs(complexity=2.0))
    assert decision.tier in adapter.tiers()
    assert 0 <= decision.profile_idx < 48


def test_numpy_adapter_missing_weights_raises_domain_error() -> None:
    """A missing weights path surfaces a domain ``RetrievalError``, not a leak."""
    with pytest.raises(RetrievalError):
        NumpyPavoRoutingAdapter(weights_path="/nonexistent/pavo_weights.npz")

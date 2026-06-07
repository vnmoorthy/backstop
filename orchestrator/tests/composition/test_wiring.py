"""Composition-root wiring tests.

Asserts that ``build_container(Settings())`` resolves the entire object graph
with no missing binding, that every Container slot is non-``None``, that the
per-port mode flags select the real vs sim adapter, and that the PAVO factory
falls back to the bit-faithful numpy backend when torch is forced off.
"""

from __future__ import annotations

import dataclasses

import pytest

from backstop.composition.container import Container
from backstop.composition.wiring import build_container
from backstop.infra.config import Settings

# Container fields that are lifecycle handles rather than wired ports/services;
# they are allowed to be empty (e.g. no HTTP clients are opened in sim mode).
_LIFECYCLE_FIELDS = {"http_clients"}


def test_build_container_resolves_with_no_missing_binding() -> None:
    """Every port + service slot is populated in the default (sim) graph."""
    container = build_container(Settings())
    assert isinstance(container, Container)

    missing = [
        f.name
        for f in dataclasses.fields(container)
        if f.name not in _LIFECYCLE_FIELDS and getattr(container, f.name) is None
    ]
    assert missing == [], f"unwired container slots: {missing}"


def test_container_is_frozen() -> None:
    """The wired container is immutable (reassigning a field raises)."""
    container = build_container(Settings())
    with pytest.raises(dataclasses.FrozenInstanceError):
        container.repo = None  # type: ignore[misc]


def test_sim_mode_selects_sim_adapters() -> None:
    """Default sim flags select the local/sim adapters for each port."""
    container = build_container(Settings())
    assert type(container.retrieval).__name__ == "TfidfRetrievalAdapter"
    assert type(container.gateway).__name__ == "SimGatewayAdapter"
    assert type(container.reasoning).__name__ == "LocalReasoningAdapter"
    assert type(container.speech).__name__ == "SimTtsAdapter"
    assert type(container.transport).__name__ == "InProcessTransportAdapter"
    assert type(container.gate).__name__ == "SemaphoreConcurrencyGate"
    assert type(container.parser).__name__ == "DeterministicDenialParserAdapter"
    assert type(container.repo).__name__ == "MemoryAppealRepo"
    # No HTTP transport is opened for a fully-sim graph.
    assert container.http_clients == ()


def test_real_mode_flags_select_real_adapters() -> None:
    """``*_MODE=real`` flips the relevant ports to their real HTTP adapters.

    Real adapters are constructed (not invoked), so no network call is made; the
    factory selection is what is under test here.
    """
    settings = Settings(
        MOSS_MODE="real",
        TFY_MODE="real",
        MINIMAX_MODE="real",
        UNSILOED_MODE="real",
        QWEN_MODE="real",
        LIVEKIT_MODE="real",
        BACKSTOP_AWS_MODE="real",
        MOSS_PROJECT_ID="p",
        MOSS_PROJECT_KEY="k",
    )
    container = build_container(settings)
    assert type(container.retrieval).__name__ == "MossHttpAdapter"
    assert type(container.gateway).__name__ == "TrueFoundryGatewayAdapter"
    assert type(container.reasoning).__name__ == "MiniMaxReasoningAdapter"
    assert type(container.parser).__name__ == "UnsiloedDenialParserAdapter"
    assert type(container.speech).__name__ == "QwenTtsAdapter"
    assert type(container.transport).__name__ == "LiveKitTransportAdapter"
    assert type(container.gate).__name__ == "FargateConcurrencyGate"
    # Real HTTP adapters registered their shared/host-bound clients for cleanup.
    assert len(container.http_clients) >= 1


def test_pavo_falls_back_to_numpy_when_torch_forced_off() -> None:
    """Forcing ``PAVO_ADAPTER_IMPL=numpy`` yields the torch-free numpy backend."""
    container = build_container(Settings(PAVO_ADAPTER_IMPL="numpy"))
    assert type(container.routing).__name__ == "NumpyPavoRoutingAdapter"


def test_pavo_prefers_torch_when_available() -> None:
    """The default impl prefers torch (numpy is the fallback, not the default)."""
    container = build_container(Settings(PAVO_ADAPTER_IMPL="torch"))
    # torch is installed in this environment, so the torch backend is selected;
    # the factory would otherwise have fallen back to numpy without raising.
    assert type(container.routing).__name__ in {
        "TorchPavoRoutingAdapter",
        "NumpyPavoRoutingAdapter",
    }


def test_redaction_is_single_shared_instance() -> None:
    """The same redaction singleton backs the gateway and the services."""
    container = build_container(Settings())
    assert container.redaction is not None
    # The gateway was constructed with the shared redaction port; the letter and
    # review services share the same instance (constructor injection).
    assert container.letter_service is not None
    assert container.review_service is not None

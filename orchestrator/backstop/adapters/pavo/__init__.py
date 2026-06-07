"""PAVO routing adapters — the ``RoutingPort`` implementations.

Two adapters honour the identical :class:`~backstop.ports.routing_port.RoutingPort`
Protocol with a bit-faithful, frozen policy:

* :class:`~backstop.adapters.pavo.torch_routing_adapter.TorchPavoRoutingAdapter`
  — the REAL adapter; wraps the vendored TMLR ``MetaController`` torch forward.
* :class:`~backstop.adapters.pavo.numpy_routing_adapter.NumpyPavoRoutingAdapter`
  — the SIM/fallback adapter; a pure-NumPy reimplementation of the same MLP
  forward over the SAME frozen weights (argmax bit-faithful to torch).

Neither adapter makes a network call, holds a key, or ever sees PHI: routing is
pure decisioning over the twelve-dimension :class:`TurnObservation` telemetry.
The shared encoder, 48->3 collapse, and coupling-mask feasibility live in
:mod:`backstop.adapters.pavo._coupling` so the two adapters stay byte-identical
on policy and differ only in the linear-algebra backend.
"""

from __future__ import annotations

__all__ = [
    "TorchPavoRoutingAdapter",
    "NumpyPavoRoutingAdapter",
]


def __getattr__(name: str) -> object:
    """Lazily resolve the adapter classes so importing the package is cheap.

    The torch adapter imports the vendor SDK lazily inside its constructor, but
    the numpy adapter is always available; resolving both lazily here keeps the
    package import free of any heavy work.
    """
    if name == "TorchPavoRoutingAdapter":
        from backstop.adapters.pavo.torch_routing_adapter import (
            TorchPavoRoutingAdapter,
        )

        return TorchPavoRoutingAdapter
    if name == "NumpyPavoRoutingAdapter":
        from backstop.adapters.pavo.numpy_routing_adapter import (
            NumpyPavoRoutingAdapter,
        )

        return NumpyPavoRoutingAdapter
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

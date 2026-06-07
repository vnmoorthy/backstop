"""Unsiloed denial-parser adapters (real HTTP + deterministic sim).

Two concrete bindings of
:class:`~backstop.ports.denial_parser_port.DenialParserPort` that speak the same
``DenialExtraction`` shape:

* :class:`~backstop.adapters.unsiloed.unsiloed_http_adapter.\
UnsiloedDenialParserAdapter` — the real adapter (async create+poll against the
  Unsiloed vision API, bytes-only, lazy ``httpx`` import).
* :class:`~backstop.adapters.unsiloed.deterministic_parser_adapter.\
DeterministicDenialParserAdapter` — the sim adapter (genuine offline X12/EOB
  parsing, the universal fallback).

The package also owns the shared canonical Denial field vocabulary
(:mod:`backstop.adapters.unsiloed._denial_schema`) and the typed adapter errors
(:mod:`backstop.adapters.unsiloed.errors`).
"""

from __future__ import annotations

__all__ = [
    "DeterministicDenialParserAdapter",
    "UnsiloedDenialParserAdapter",
]


def __getattr__(name: str) -> object:
    """Lazily expose the two adapters without importing ``httpx`` eagerly.

    Re-exporting the real adapter at package import time would drag the module
    (and its ``TYPE_CHECKING`` httpx reference) into every importer; deferring
    the import keeps ``import backstop.adapters.unsiloed`` cheap and SDK-free.
    """
    if name == "DeterministicDenialParserAdapter":
        from backstop.adapters.unsiloed.deterministic_parser_adapter import (
            DeterministicDenialParserAdapter,
        )

        return DeterministicDenialParserAdapter
    if name == "UnsiloedDenialParserAdapter":
        from backstop.adapters.unsiloed.unsiloed_http_adapter import (
            UnsiloedDenialParserAdapter,
        )

        return UnsiloedDenialParserAdapter
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

"""Id generation port: the single injected source of new identifiers.

Defines the :class:`IdGenPort` protocol. Services mint appeal ids, call ids,
artifact refs, and audit ids through this injected singleton rather than calling
``uuid``/``random`` directly, so tests can substitute a deterministic counter and
make id-dependent behaviour reproducible.

Implemented by ``UuidIdGenAdapter`` (uuid4) and a deterministic counter in tests.
This module imports only the standard library and performs no I/O.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class IdGenPort(Protocol):
    """Injected identifier generator.

    Services name this protocol and never call ``uuid``/``random`` directly.
    """

    def new_id(self) -> str:
        """Return a fresh, unique string identifier.

        The real adapter returns a uuid4 string; the test adapter returns a
        deterministic, monotonically increasing counter value. Each call yields a
        value distinct from all prior calls within the process.
        """
        ...

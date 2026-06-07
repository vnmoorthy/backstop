"""Clock port: the single injected source of time for determinism.

Defines the :class:`ClockPort` protocol. Every service reads wall-clock time and
monotonic elapsed time through this injected singleton rather than calling
``datetime``/``time`` directly, so tests can substitute a ``FakeClock``/freezegun
and SOL-deadline / TTL logic stays deterministic.

Implemented by ``SystemClockAdapter`` (real) and a fake clock in tests. This
module imports only the standard library and performs no I/O.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol, runtime_checkable


@runtime_checkable
class ClockPort(Protocol):
    """Injected clock providing wall-clock and monotonic time.

    Services name this protocol and never call ``datetime.now``/``time`` directly.
    """

    def now(self) -> datetime:
        """Return the current wall-clock time as a timezone-aware ``datetime``.

        Used for timestamps, SOL-deadline evaluation, and TTL expiry. In tests a
        fake clock returns a fixed/controlled value.
        """
        ...

    def monotonic(self) -> float:
        """Return a monotonically increasing seconds counter.

        Suitable for measuring elapsed time / latency; the value has no relation
        to wall-clock time and never decreases.
        """
        ...

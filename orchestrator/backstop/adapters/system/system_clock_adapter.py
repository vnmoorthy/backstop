"""Real wall-clock + monotonic adapter for :class:`ClockPort`.

The single sanctioned source of time in production. Every service reads time
through this injected singleton rather than calling :mod:`datetime`/:mod:`time`
directly, so tests can substitute a frozen clock and SOL-deadline / TTL logic
stays deterministic. This module imports only the standard library and performs
no I/O.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone

__all__ = ["SystemClockAdapter"]


class SystemClockAdapter:
    """Production :class:`~backstop.ports.clock_port.ClockPort` implementation.

    ``now`` returns a timezone-aware UTC ``datetime``; ``monotonic`` returns the
    process monotonic seconds counter. There is no hidden state, so the adapter
    is trivially shareable as a process-wide singleton.
    """

    def now(self) -> datetime:
        """Return the current wall-clock time as a timezone-aware UTC datetime."""
        return datetime.now(timezone.utc)

    def monotonic(self) -> float:
        """Return a monotonically increasing seconds counter (never decreases)."""
        return time.monotonic()

"""In-process ``asyncio.Semaphore`` admission gate (the SIM ConcurrencyGatePort).

This is the sim-mode :class:`~backstop.ports.concurrency_gate_port.ConcurrencyGatePort`
and it does **real local backpressure**, not a string echo. A bounded
``asyncio.Semaphore`` is the genuine capacity budget the swarm blocks on: when more
than ``max_concurrency`` appeal-workers try to enter, the surplus coroutines actually
*suspend* until a holder calls :meth:`release`, so the (max+1)th acquire only proceeds
after a slot frees. It shares the exact ``Slot`` / ``CapacitySnapshot`` /
``CapacityTimeout`` contract as the real Fargate adapter, so the Service layer behaves
identically whether AWS credentials are present or not — giving real OOM/overload
protection even with no cloud.

The gate never sees PHI: ``slot_key`` is a non-PHI surrogate (the appeal id) used only
for tracing and to stamp the returned :class:`Slot`.
"""

from __future__ import annotations

import asyncio
import uuid
from contextlib import asynccontextmanager
from typing import AsyncIterator, Optional, Set

from backstop.domain.enums import IntegrationMode
from backstop.domain.errors import CapacityTimeout
from backstop.ports.concurrency_gate_port import CapacitySnapshot, Slot

__all__ = ["SemaphoreConcurrencyGate"]

_BACKEND_REF = "sim"


class SemaphoreConcurrencyGate:
    """Bounded local concurrency gate backed by a real ``asyncio.Semaphore``.

    Structurally implements :class:`ConcurrencyGatePort`. Construction is done by the
    composition root, which passes the hard ceiling ``max_concurrency`` (the same
    ``BACKSTOP_MAX_CONCURRENCY`` budget the real adapter caps against).
    """

    def __init__(self, *, max_concurrency: int) -> None:
        """Build the gate with a fixed ceiling of ``max_concurrency`` slots.

        Args:
            max_concurrency: Hard cap on simultaneously held slots; must be >= 1.

        Raises:
            ValueError: If ``max_concurrency`` is not a positive integer.
        """
        if max_concurrency < 1:
            raise ValueError(f"max_concurrency must be >= 1, got {max_concurrency}")
        self._max = max_concurrency
        self._sem = asyncio.Semaphore(max_concurrency)
        # Short-critical-section lock guarding the in-use counter and released set;
        # never held across the (potentially blocking) semaphore acquire.
        self._lock = asyncio.Lock()
        self._in_use = 0
        self._released: Set[str] = set()
        self._closed = False

    async def acquire(self, *, slot_key: str, timeout: Optional[float] = None) -> Slot:
        """Acquire a slot for ``slot_key``, suspending up to ``timeout`` seconds.

        When the gate is full the calling coroutine genuinely suspends on the
        semaphore until :meth:`release` frees a permit. ``timeout=None`` waits
        indefinitely.

        Args:
            slot_key: Non-PHI surrogate (the appeal id) the slot is keyed by.
            timeout: Maximum seconds to wait for a free slot; ``None`` waits forever.

        Returns:
            An opened :class:`Slot` whose ``slot_key`` matches the request.

        Raises:
            CapacityTimeout: If no slot frees before ``timeout`` elapses, or if the
                gate has been closed via :meth:`aclose`.
        """
        if self._closed:
            raise CapacityTimeout("concurrency gate is closed")
        try:
            if timeout is None:
                await self._sem.acquire()
            else:
                await asyncio.wait_for(self._sem.acquire(), timeout)
        except asyncio.TimeoutError as exc:
            raise CapacityTimeout(
                f"no slot available within {timeout}s (capacity={self._max})"
            ) from exc

        async with self._lock:
            self._in_use += 1
        return Slot(slot_id=uuid.uuid4().hex, slot_key=slot_key, backend_ref=_BACKEND_REF)

    async def release(self, slot: Slot) -> None:
        """Return ``slot`` to the pool; idempotent (a second release is a no-op).

        Tracking released ``slot_id`` values means releasing the same slot twice frees
        exactly one permit, never two — so a double-release cannot over-credit capacity.
        """
        async with self._lock:
            if slot.slot_id in self._released:
                return
            self._released.add(slot.slot_id)
            self._in_use -= 1
        self._sem.release()

    @asynccontextmanager
    async def slot(self, slot_key: str) -> AsyncIterator[Slot]:
        """Acquire/release a slot as an async context manager.

        Release is guaranteed in the ``finally`` arm, so the slot is returned even when
        the wrapped body raises.
        """
        acquired = await self.acquire(slot_key=slot_key)
        try:
            yield acquired
        finally:
            await self.release(acquired)

    async def ensure_capacity(self, *, target: int) -> int:
        """Clamp ``target`` to the ceiling and report the resulting capacity.

        Pre-warming is free in-process, so this is a no-op beyond honestly reporting
        ``min(target, max_concurrency)`` — it never claims more than the ceiling.
        """
        return min(max(target, 0), self._max)

    async def capacity(self) -> CapacitySnapshot:
        """Return a point-in-time capacity snapshot (``mode`` is always ``SIM``)."""
        async with self._lock:
            in_use = self._in_use
        return CapacitySnapshot(
            capacity=self._max,
            in_use=in_use,
            available=self._max - in_use,
            mode=IntegrationMode.SIM,
        )

    async def reconcile(self) -> int:
        """Return the authoritative in-process in-use count (nothing to recover)."""
        async with self._lock:
            return self._in_use

    async def aclose(self) -> None:
        """Mark the gate closed so further :meth:`acquire` calls are refused."""
        self._closed = True

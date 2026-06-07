"""ConcurrencyGatePort — AWS admission control (L2 port).

Caps global swarm concurrency: each appeal acquires a slot via
``async with gate.slot(appeal_id)`` before its PAVO loop starts. The sim adapter
is a real ``asyncio.Semaphore`` whose (max+1)th acquire genuinely suspends; the
real Fargate adapter backs slots with warm ECS tasks. The gate never sees PHI —
``slot_key`` is a non-PHI surrogate (the appeal id).

This module defines the Protocol plus its ``Slot`` / ``CapacitySnapshot`` DTOs only;
concrete adapters live in ``backstop.adapters.aws``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import AsyncContextManager, Optional, Protocol, runtime_checkable

from backstop.domain.enums import IntegrationMode

__all__ = [
    "Slot",
    "CapacitySnapshot",
    "ConcurrencyGatePort",
]


@dataclass(frozen=True)
class Slot:
    """An acquired admission slot held for the duration of one appeal's work.

    Attributes:
        slot_id: Unique identifier for this held slot.
        slot_key: The non-PHI surrogate key (the appeal id) the slot is keyed by.
        backend_ref: Optional backend handle (e.g. an ECS task ARN), when real.
    """

    slot_id: str
    slot_key: str
    backend_ref: Optional[str] = None


@dataclass(frozen=True)
class CapacitySnapshot:
    """A point-in-time view of gate capacity for the concurrency meter.

    Attributes:
        capacity: The maximum number of simultaneous slots.
        in_use: The number of slots currently held.
        available: Slots free to acquire (``capacity - in_use``).
        mode: Whether the active adapter is real or sim.
    """

    capacity: int
    in_use: int
    available: int
    mode: IntegrationMode


@runtime_checkable
class ConcurrencyGatePort(Protocol):
    """Async admission control capping global swarm concurrency."""

    async def acquire(
        self, *, slot_key: str, timeout: Optional[float] = None
    ) -> Slot:
        """Acquire a slot for ``slot_key``, blocking up to ``timeout`` seconds.

        Raises:
            CapacityTimeout: If no slot becomes available before ``timeout``.
        """
        ...

    async def release(self, slot: Slot) -> None:
        """Release ``slot`` back to the pool; idempotent."""
        ...

    def slot(self, slot_key: str) -> AsyncContextManager[Slot]:
        """Acquire/release a slot as a context manager; release is guaranteed."""
        ...

    async def ensure_capacity(self, *, target: int) -> int:
        """Ensure at least ``target`` capacity; return the resulting capacity."""
        ...

    async def capacity(self) -> CapacitySnapshot:
        """Return a point-in-time capacity snapshot."""
        ...

    async def reconcile(self) -> int:
        """Reconcile backend state on startup; return reclaimed slot count."""
        ...

    async def aclose(self) -> None:
        """Gracefully release all gate resources."""
        ...

"""Tests for :class:`SwarmService` admission discipline.

Pins, against a genuine ``asyncio.Semaphore`` gate:

* Each appeal acquires a slot before its body runs (``async with gate.slot``).
* Concurrency never exceeds the cap (high-water mark <= capacity).
* Every slot is released — ``capacity().in_use == 0`` at the end, even when a
  body raises (the ``finally`` in the slot context manager).
"""

from __future__ import annotations

import asyncio

from backstop.services.swarm_service import SwarmService
from tests.services.fakes import SemaphoreGate


async def test_never_exceeds_capacity() -> None:
    """With cap=2 and 5 appeals, at most 2 bodies run concurrently."""
    gate = SemaphoreGate(capacity=2)
    service = SwarmService(gate)
    barrier = asyncio.Event()
    running = 0
    peak = 0

    async def body(appeal_id: str) -> None:
        nonlocal running, peak
        running += 1
        peak = max(peak, running)
        # Hold the slot briefly so concurrency can actually overlap.
        await asyncio.sleep(0.01)
        running -= 1

    appeal_ids = [f"appeal-{i}" for i in range(5)]
    outcomes = await service.run_all(appeal_ids, body)
    barrier.set()

    assert peak <= 2
    assert gate.max_in_use <= 2
    assert all(o.ok for o in outcomes)
    snapshot = await gate.capacity()
    assert snapshot.in_use == 0


async def test_slot_released_on_exception() -> None:
    """A body that raises still releases its slot; peers still complete."""
    gate = SemaphoreGate(capacity=2)
    service = SwarmService(gate)

    async def body(appeal_id: str) -> None:
        await asyncio.sleep(0.005)
        if appeal_id == "appeal-2":
            raise RuntimeError("boom")

    appeal_ids = [f"appeal-{i}" for i in range(5)]
    outcomes = await service.run_all(appeal_ids, body)

    # All slots released despite the failure.
    snapshot = await gate.capacity()
    assert snapshot.in_use == 0
    assert gate.max_in_use <= 2

    by_id = {o.appeal_id: o for o in outcomes}
    assert by_id["appeal-2"].ok is False
    assert by_id["appeal-2"].error == "boom"
    # The other four lanes succeeded; one failure does not cancel peers.
    assert sum(1 for o in outcomes if o.ok) == 4


async def test_every_appeal_acquires_a_slot() -> None:
    """Each appeal id keys exactly one acquire (one slot per body)."""
    gate = SemaphoreGate(capacity=3)
    service = SwarmService(gate)
    seen: list[str] = []

    async def body(appeal_id: str) -> None:
        seen.append(appeal_id)

    appeal_ids = [f"appeal-{i}" for i in range(6)]
    await service.run_all(appeal_ids, body)

    assert sorted(seen) == sorted(appeal_ids)
    snapshot = await gate.capacity()
    assert snapshot.in_use == 0


async def test_empty_input_is_noop() -> None:
    """An empty appeal list returns no outcomes and holds no slots."""
    gate = SemaphoreGate(capacity=2)
    service = SwarmService(gate)

    async def body(appeal_id: str) -> None:  # pragma: no cover - never called
        raise AssertionError("body must not run")

    outcomes = await service.run_all([], body)

    assert outcomes == ()
    snapshot = await gate.capacity()
    assert snapshot.in_use == 0

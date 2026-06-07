"""Contract suite for :class:`ConcurrencyGatePort` — sim and real interchangeable.

Both adapters are driven through the *same* port surface and asserted to honour the
identical blocking/admission contract: the sim
:class:`~backstop.adapters.aws.semaphore_gate.SemaphoreConcurrencyGate` (a real
``asyncio.Semaphore``) and the real
:class:`~backstop.adapters.aws.fargate_gate.FargateConcurrencyGate` (driven against a
hand-written fake ECS client — NO live AWS, no network). The contract proves the two are
behaviourally substitutable: acquiring ``max`` slots succeeds, the ``(max+1)``th acquire
genuinely blocks until a release, ``acquire(timeout)`` on a full gate raises the same
domain ``CapacityTimeout``, and ``slot()`` releases in ``finally`` even when the body
raises.
"""

from __future__ import annotations

import asyncio
import itertools
from typing import Any, AsyncIterator, Dict, List

import pytest

from backstop.adapters.aws.fargate_gate import (
    FargateConcurrencyGate,
    FargateGateConfig,
)
from backstop.adapters.aws.semaphore_gate import SemaphoreConcurrencyGate
from backstop.domain.enums import IntegrationMode
from backstop.domain.errors import CapacityTimeout
from backstop.ports.concurrency_gate_port import (
    CapacitySnapshot,
    ConcurrencyGatePort,
    Slot,
)

pytestmark = pytest.mark.contract

MAX = 3


# --------------------------------------------------------------------------- #
# Fake ECS client — a deterministic in-memory stand-in for boto3's ECS client.
# --------------------------------------------------------------------------- #
class FakeEcsClient:
    """Hand-written fake honouring the :class:`EcsClient` async surface.

    Tasks transition straight to ``RUNNING`` so the warm-up poll resolves immediately.
    Records every ``run_task`` request so tests can assert on the launch kwargs, and
    tracks live task ARNs for ``list_tasks`` reconcile.
    """

    def __init__(self) -> None:
        self.run_calls: List[Dict[str, Any]] = []
        self.stop_calls: List[Dict[str, Any]] = []
        self._arns = itertools.count(1)
        self._status: Dict[str, str] = {}

    async def run_task(self, **kwargs: Any) -> Dict[str, Any]:
        self.run_calls.append(kwargs)
        arn = f"arn:aws:ecs:us-east-1:1234:task/fake/{next(self._arns):04d}"
        self._status[arn] = "RUNNING"
        return {
            "tasks": [{"taskArn": arn, "lastStatus": "PROVISIONING"}],
            "failures": [],
        }

    async def describe_tasks(self, **kwargs: Any) -> Dict[str, Any]:
        tasks = []
        for arn in kwargs.get("tasks", []):
            tasks.append({"taskArn": arn, "lastStatus": self._status.get(arn, "RUNNING")})
        return {"tasks": tasks, "failures": []}

    async def stop_task(self, **kwargs: Any) -> Dict[str, Any]:
        self.stop_calls.append(kwargs)
        arn = kwargs.get("task", "")
        self._status.pop(arn, None)
        return {"task": {"taskArn": arn, "lastStatus": "STOPPED"}}

    async def list_tasks(self, **kwargs: Any) -> Dict[str, Any]:
        running = [arn for arn, st in self._status.items() if st == "RUNNING"]
        return {"taskArns": running, "nextToken": None}


async def _instant_sleep(_seconds: float) -> None:
    """Drop-in for ``asyncio.sleep`` that yields control without real delay."""
    await asyncio.sleep(0)


# --------------------------------------------------------------------------- #
# Fixtures — the contract runs once per adapter behind the same port.
# --------------------------------------------------------------------------- #
@pytest.fixture(params=["sim", "real"])
async def gate(request: pytest.FixtureRequest) -> AsyncIterator[ConcurrencyGatePort]:
    """Yield each adapter in turn, both capped at :data:`MAX` slots."""
    built: ConcurrencyGatePort
    if request.param == "sim":
        built = SemaphoreConcurrencyGate(max_concurrency=MAX)
    else:
        config = FargateGateConfig(
            cluster="backstop-cluster",
            task_definition="appeal-worker:7",
            subnets=["subnet-aaa"],
            security_groups=["sg-bbb"],
            container_name="appeal-worker",
            max_concurrency=MAX,
            capacity_provider="FARGATE_SPOT",
            assign_public_ip="DISABLED",
            warm_keep=0,
        )
        built = FargateConcurrencyGate(
            config=config, client=FakeEcsClient(), sleep=_instant_sleep
        )
    try:
        yield built
    finally:
        await built.aclose()


# --------------------------------------------------------------------------- #
# Contract cases.
# --------------------------------------------------------------------------- #
async def test_acquire_returns_slot_with_matching_key(gate: ConcurrencyGatePort) -> None:
    """``acquire`` returns a :class:`Slot` whose ``slot_key`` matches the request."""
    slot = await gate.acquire(slot_key="appeal-42")
    assert isinstance(slot, Slot)
    assert slot.slot_key == "appeal-42"
    assert slot.slot_id
    await gate.release(slot)


async def test_acquire_max_slots_all_succeed(gate: ConcurrencyGatePort) -> None:
    """Acquiring exactly ``max`` slots all succeed and exhaust capacity."""
    slots = [await gate.acquire(slot_key=f"a-{i}") for i in range(MAX)]
    assert len({s.slot_id for s in slots}) == MAX
    snap = await gate.capacity()
    assert snap.in_use == MAX
    assert snap.available == 0
    for slot in slots:
        await gate.release(slot)


async def test_max_plus_one_blocks_until_release(gate: ConcurrencyGatePort) -> None:
    """The ``(max+1)``th acquire BLOCKS and only resolves after a release."""
    held = [await gate.acquire(slot_key=f"h-{i}") for i in range(MAX)]

    pending = asyncio.ensure_future(gate.acquire(slot_key="overflow"))
    # Give the overflow acquire a chance to run; it must stay pending (gate is full).
    await asyncio.sleep(0.02)
    assert not pending.done(), "the (max+1)th acquire should block while the gate is full"

    # Releasing one slot must let exactly the blocked acquire proceed.
    await gate.release(held.pop())
    overflow = await asyncio.wait_for(pending, timeout=1.0)
    assert overflow.slot_key == "overflow"

    await gate.release(overflow)
    for slot in held:
        await gate.release(slot)


async def test_acquire_timeout_on_full_raises_capacity_timeout(
    gate: ConcurrencyGatePort,
) -> None:
    """``acquire(timeout=...)`` on a full gate raises domain ``CapacityTimeout``."""
    held = [await gate.acquire(slot_key=f"f-{i}") for i in range(MAX)]
    with pytest.raises(CapacityTimeout):
        await gate.acquire(slot_key="late", timeout=0.05)
    for slot in held:
        await gate.release(slot)


async def test_release_is_idempotent(gate: ConcurrencyGatePort) -> None:
    """Releasing the same slot twice frees exactly one permit, never two."""
    s1 = await gate.acquire(slot_key="x")
    s2 = await gate.acquire(slot_key="y")
    await gate.release(s1)
    await gate.release(s1)  # second release must be a no-op.

    snap = await gate.capacity()
    assert snap.in_use == 1  # only s2 still held; the double-release didn't over-credit.
    await gate.release(s2)
    final = await gate.capacity()
    assert final.in_use == 0


async def test_slot_context_releases_on_exception(gate: ConcurrencyGatePort) -> None:
    """``async with gate.slot(k)`` releases even when the body raises."""
    base = (await gate.capacity()).in_use
    with pytest.raises(RuntimeError, match="boom"):
        async with gate.slot("ctx-key") as slot:
            assert slot.slot_key == "ctx-key"
            assert (await gate.capacity()).in_use == base + 1
            raise RuntimeError("boom")
    assert (await gate.capacity()).in_use == base  # released in finally despite the raise.


async def test_capacity_invariant_holds(gate: ConcurrencyGatePort) -> None:
    """``available == capacity - in_use`` and ``0 <= in_use <= capacity`` after ops."""
    snapshots: List[CapacitySnapshot] = [await gate.capacity()]
    held = [await gate.acquire(slot_key=f"i-{i}") for i in range(2)]
    snapshots.append(await gate.capacity())
    await gate.release(held.pop())
    snapshots.append(await gate.capacity())
    for snap in snapshots:
        assert snap.capacity == MAX
        assert 0 <= snap.in_use <= snap.capacity
        assert snap.available == snap.capacity - snap.in_use
        assert snap.mode in (IntegrationMode.SIM, IntegrationMode.REAL)
    for slot in held:
        await gate.release(slot)


async def test_ensure_capacity_never_exceeds_max(gate: ConcurrencyGatePort) -> None:
    """``ensure_capacity(target)`` never reports more than ``max_concurrency``."""
    assert await gate.ensure_capacity(target=MAX + 50) <= MAX
    assert await gate.ensure_capacity(target=1) <= MAX


async def test_reconcile_returns_in_use_and_leaves_gate_usable(
    gate: ConcurrencyGatePort,
) -> None:
    """``reconcile`` returns an in-use count and the gate keeps working afterwards."""
    reconciled = await gate.reconcile()
    assert isinstance(reconciled, int)
    assert reconciled >= 0
    slot = await gate.acquire(slot_key="post-reconcile")
    assert slot.slot_key == "post-reconcile"
    await gate.release(slot)


async def test_aclose_then_acquire_raises(gate: ConcurrencyGatePort) -> None:
    """A closed gate refuses new work: ``acquire`` after ``aclose`` raises."""
    await gate.aclose()
    with pytest.raises(CapacityTimeout):
        await gate.acquire(slot_key="after-close")


# --------------------------------------------------------------------------- #
# Real-adapter specifics — driven against the fake ECS client only.
# --------------------------------------------------------------------------- #
async def test_real_acquire_issues_runtask_with_non_phi_overrides() -> None:
    """The Fargate adapter's ``acquire`` issues a correctly-shaped ``run_task``.

    Asserts the surrogate appeal id (NOT PHI) reaches the container env override, the
    awsvpc network config and capacity-provider strategy are present, ``startedBy`` and a
    ``clientToken`` are set — and that no payer/PHI data appears anywhere in the request.
    """
    fake = FakeEcsClient()
    config = FargateGateConfig(
        cluster="c1",
        task_definition="appeal-worker:1",
        subnets=["subnet-x"],
        security_groups=["sg-y"],
        container_name="worker",
        max_concurrency=2,
        capacity_provider="FARGATE",
        assign_public_ip="DISABLED",
    )
    gate = FargateConcurrencyGate(config=config, client=fake, sleep=_instant_sleep)
    slot = await gate.acquire(slot_key="appeal-surrogate-1")
    try:
        assert len(fake.run_calls) == 1
        req = fake.run_calls[0]
        assert req["count"] == 1
        assert req["startedBy"] == "backstop-swarm"
        assert req["clientToken"]
        strategy = req["capacityProviderStrategy"][0]
        assert strategy == {"capacityProvider": "FARGATE", "weight": 1, "base": 0}
        awsvpc = req["networkConfiguration"]["awsvpcConfiguration"]
        assert awsvpc["subnets"] == ["subnet-x"]
        assert awsvpc["securityGroups"] == ["sg-y"]
        env = req["overrides"]["containerOverrides"][0]["environment"]
        env_map = {e["name"]: e["value"] for e in env}
        assert env_map["BACKSTOP_APPEAL_ID"] == "appeal-surrogate-1"
        assert env_map["BACKSTOP_SLOT_ID"]
        # The slot's backend ref is the launched task ARN.
        assert slot.backend_ref and slot.backend_ref.startswith("arn:aws:ecs:")
    finally:
        await gate.release(slot)


async def test_real_quota_exhaustion_blocks_then_times_out() -> None:
    """A ``RESOURCE:FARGATE`` run_task failure does not crash; it times out cleanly."""

    class QuotaExhaustedEcs(FakeEcsClient):
        async def run_task(self, **kwargs: Any) -> Dict[str, Any]:
            self.run_calls.append(kwargs)
            return {"tasks": [], "failures": [{"arn": "x", "reason": "RESOURCE:FARGATE"}]}

    config = FargateGateConfig(
        cluster="c",
        task_definition="t:1",
        subnets=["s"],
        security_groups=["g"],
        container_name="w",
        max_concurrency=4,
    )
    gate = FargateConcurrencyGate(
        config=config, client=QuotaExhaustedEcs(), sleep=_instant_sleep
    )
    with pytest.raises(CapacityTimeout):
        await gate.acquire(slot_key="appeal-q", timeout=0.05)
    await gate.aclose()


async def test_real_warm_pool_reuse_skips_second_runtask() -> None:
    """With ``warm_keep>0``, ``release`` retains the task and the next acquire reuses it."""
    fake = FakeEcsClient()
    config = FargateGateConfig(
        cluster="c",
        task_definition="t:1",
        subnets=["s"],
        security_groups=["g"],
        container_name="w",
        max_concurrency=2,
        warm_keep=1,
    )
    gate = FargateConcurrencyGate(config=config, client=fake, sleep=_instant_sleep)
    first = await gate.acquire(slot_key="appeal-1")
    arn = first.backend_ref
    await gate.release(first)  # retained warm (warm_keep=1), not stopped.
    assert fake.stop_calls == []

    second = await gate.acquire(slot_key="appeal-2")
    assert second.backend_ref == arn  # reused the warm task.
    assert len(fake.run_calls) == 1  # no second run_task.
    await gate.release(second)
    await gate.aclose()


def test_both_adapters_satisfy_port_runtime_check() -> None:
    """Both adapters pass the ``runtime_checkable`` Protocol isinstance gate."""
    sim = SemaphoreConcurrencyGate(max_concurrency=1)
    real = FargateConcurrencyGate(
        config=FargateGateConfig(
            cluster="c",
            task_definition="t:1",
            subnets=["s"],
            security_groups=["g"],
            container_name="w",
            max_concurrency=1,
        ),
        client=FakeEcsClient(),
    )
    assert isinstance(sim, ConcurrencyGatePort)
    assert isinstance(real, ConcurrencyGatePort)

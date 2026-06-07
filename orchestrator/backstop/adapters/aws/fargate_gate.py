"""AWS Fargate warm-pool admission gate (the REAL ConcurrencyGatePort).

This is the real-mode :class:`~backstop.ports.concurrency_gate_port.ConcurrencyGatePort`.
AWS's load-bearing role here is **concurrency admission**, not running each appeal
inside Fargate: the appeal logic (PAVO loop, retrieval, reasoning) runs in the
orchestrator process, and the Fargate task pool is the *capacity budget* authorising how
many of those loops may run at once. So :meth:`acquire` checks out a warm task or calls
``ecs.RunTask`` and polls ``ecs.DescribeTasks`` to ``RUNNING``; :meth:`release` returns
the task to a small warm pool or ``ecs.StopTask``s it.

Vendor I/O is isolated to an injected async ECS *client* (an object exposing
``run_task`` / ``describe_tasks`` / ``stop_task`` / ``list_tasks`` coroutines). When the
composition root passes ``client=None`` the gate lazily builds a default client that
wraps a synchronous ``boto3`` ECS client in a thread executor — the ``boto3`` import
happens **inside** :func:`default_ecs_client` so this module imports cleanly even when
the SDK is absent (sim deployments, CI without the ``real`` extra). The contract test
injects a fake client instead, so no live AWS is ever touched.

The internal model is a set of ``warm`` (idle, RUNNING) task ARNs and a set of
``in_use`` task ARNs guarded by an ``asyncio.Lock`` held only for the brief set
mutation — **never** across an ``await`` to AWS — so the swarm stays genuinely parallel.
The gate never sees PHI: only the surrogate ``slot_key`` (appeal id) and a slot uuid
flow into task env overrides; no payer data or call content.
"""

from __future__ import annotations

import asyncio
import uuid
from contextlib import asynccontextmanager
from typing import (
    Any,
    AsyncIterator,
    Awaitable,
    Callable,
    Dict,
    List,
    Optional,
    Protocol,
    Set,
)

from backstop.domain.enums import IntegrationMode
from backstop.domain.errors import CapacityTimeout
from backstop.ports.concurrency_gate_port import CapacitySnapshot, Slot

__all__ = [
    "EcsClient",
    "FargateConcurrencyGate",
    "FargateGateConfig",
    "default_ecs_client",
]

# Constant strings the spec fixes for ListTasks filtering / StopTask reasons.
_STARTED_BY = "backstop-swarm"
_RUNNING = "RUNNING"
_STOPPED = "STOPPED"
_QUOTA_EXHAUSTED_PREFIX = "RESOURCE:FARGATE"
# Exponential backoff for the eventual-consistency DescribeTasks poll (2s -> ~30s cap).
_BACKOFF_INITIAL_S = 2.0
_BACKOFF_MAX_S = 30.0
_BACKOFF_FACTOR = 2.0


class EcsClient(Protocol):
    """Minimal async surface the gate needs from an ECS client.

    A real client wraps ``boto3``/``aioboto3``; the contract test injects a fake. Each
    method mirrors the boto3 ECS operation of the same name and returns the parsed
    response dict.
    """

    async def run_task(self, **kwargs: Any) -> Dict[str, Any]:
        """Launch one Fargate task (acquire a slot)."""
        ...

    async def describe_tasks(self, **kwargs: Any) -> Dict[str, Any]:
        """Poll task health to confirm ``RUNNING`` / detect ``STOPPED``."""
        ...

    async def stop_task(self, **kwargs: Any) -> Dict[str, Any]:
        """Stop a task (release a slot beyond the warm-pool keep)."""
        ...

    async def list_tasks(self, **kwargs: Any) -> Dict[str, Any]:
        """List live task ARNs for reconcile after an orchestrator restart."""
        ...


class FargateGateConfig:
    """Immutable launch configuration for the Fargate worker pool.

    Construction is done by the composition root from ``BACKSTOP_ECS_*`` settings; no
    PHI is ever carried here.
    """

    __slots__ = (
        "cluster",
        "task_definition",
        "subnets",
        "security_groups",
        "container_name",
        "capacity_provider",
        "assign_public_ip",
        "max_concurrency",
        "warm_keep",
    )

    def __init__(
        self,
        *,
        cluster: str,
        task_definition: str,
        subnets: List[str],
        security_groups: List[str],
        container_name: str,
        max_concurrency: int,
        capacity_provider: str = "FARGATE",
        assign_public_ip: str = "DISABLED",
        warm_keep: int = 0,
    ) -> None:
        """Capture the pool launch parameters; ``max_concurrency`` must be >= 1."""
        if max_concurrency < 1:
            raise ValueError(f"max_concurrency must be >= 1, got {max_concurrency}")
        if warm_keep < 0:
            raise ValueError(f"warm_keep must be >= 0, got {warm_keep}")
        self.cluster = cluster
        self.task_definition = task_definition
        self.subnets = list(subnets)
        self.security_groups = list(security_groups)
        self.container_name = container_name
        self.capacity_provider = capacity_provider
        self.assign_public_ip = assign_public_ip
        self.max_concurrency = max_concurrency
        self.warm_keep = warm_keep


def default_ecs_client(*, region: str) -> EcsClient:
    """Build the production ECS client wrapping synchronous ``boto3`` in an executor.

    The ``boto3`` import is performed lazily *inside* this function so importing the
    adapter module never requires the SDK. SigV4 signing, retries and endpoint
    resolution are handled by botocore from the standard credential chain.

    Args:
        region: AWS region (e.g. ``us-east-1``); from ``AWS_REGION``.

    Returns:
        An :class:`EcsClient` whose coroutines run the blocking boto3 calls in the
        default thread executor.

    Raises:
        CapacityTimeout: If the ``boto3`` SDK is not installed (the ``real`` extra is
            missing) — surfaced as a domain error so callers never see ``ImportError``.
    """
    try:
        # Lazy import: keep the boto3 SDK optional at module-import time.
        import boto3
        from botocore.config import Config
    except ImportError as exc:  # pragma: no cover - exercised only without the SDK.
        raise CapacityTimeout(
            "boto3 is required for the real Fargate gate; install the 'real' extra"
        ) from exc

    sync_client = boto3.client(
        "ecs",
        region_name=region,
        config=Config(retries={"max_attempts": 5, "mode": "adaptive"}),
    )

    return _ExecutorEcsClient(sync_client)


class _ExecutorEcsClient:
    """Adapt a blocking boto3 ECS client to the async :class:`EcsClient` surface."""

    def __init__(self, sync_client: Any) -> None:
        """Wrap ``sync_client`` (a boto3 ``ecs`` client)."""
        self._client = sync_client

    async def _call(self, op: str, **kwargs: Any) -> Dict[str, Any]:
        loop = asyncio.get_event_loop()
        method = getattr(self._client, op)
        result: Dict[str, Any] = await loop.run_in_executor(
            None, lambda: method(**kwargs)
        )
        return result

    async def run_task(self, **kwargs: Any) -> Dict[str, Any]:
        """Run ``ecs.run_task`` off the event loop."""
        return await self._call("run_task", **kwargs)

    async def describe_tasks(self, **kwargs: Any) -> Dict[str, Any]:
        """Run ``ecs.describe_tasks`` off the event loop."""
        return await self._call("describe_tasks", **kwargs)

    async def stop_task(self, **kwargs: Any) -> Dict[str, Any]:
        """Run ``ecs.stop_task`` off the event loop."""
        return await self._call("stop_task", **kwargs)

    async def list_tasks(self, **kwargs: Any) -> Dict[str, Any]:
        """Run ``ecs.list_tasks`` off the event loop."""
        return await self._call("list_tasks", **kwargs)


class FargateConcurrencyGate:
    """Warm-pool ECS admission gate; structurally a :class:`ConcurrencyGatePort`.

    Construction is done by the composition root, which injects the ECS ``client`` (or
    leaves it ``None`` so the gate lazily builds the boto3-backed default), the launch
    :class:`FargateGateConfig`, and — for testability — an async ``sleep`` used by the
    DescribeTasks backoff poll.
    """

    def __init__(
        self,
        *,
        config: FargateGateConfig,
        client: Optional[EcsClient] = None,
        region: str = "us-east-1",
        sleep: Optional[Callable[[float], Awaitable[None]]] = None,
    ) -> None:
        """Build the gate; the ECS client is created lazily if not injected."""
        self._config = config
        self._region = region
        self._client_obj = client
        self._sleep: Callable[[float], Awaitable[None]] = sleep or asyncio.sleep
        # Internal pool model — mutated only under the short-section lock.
        self._lock = asyncio.Lock()
        self._warm: Set[str] = set()
        self._in_use: Set[str] = set()
        self._released: Set[str] = set()
        # Condition a blocked acquire() waits on; release() notifies it.
        self._cond = asyncio.Condition()
        self._closed = False

    # ----------------------------------------------------------------- #
    # Lazy client resolution (boto3 stays optional at import time).
    # ----------------------------------------------------------------- #
    def _client(self) -> EcsClient:
        if self._client_obj is None:
            self._client_obj = default_ecs_client(region=self._region)
        return self._client_obj

    @property
    def _ceiling(self) -> int:
        return self._config.max_concurrency

    # ----------------------------------------------------------------- #
    # Acquire / release.
    # ----------------------------------------------------------------- #
    async def acquire(self, *, slot_key: str, timeout: Optional[float] = None) -> Slot:
        """Acquire a compute slot, blocking up to ``timeout`` seconds for one to free.

        Fast path: pop a warm (idle ``RUNNING``) task. Slow path: ``RunTask`` + poll to
        ``RUNNING`` when below the ceiling. At the ceiling (or on a ``RESOURCE:FARGATE``
        quota-exhaustion failure) the call blocks on a condition that :meth:`release`
        notifies, until ``timeout`` elapses.

        Args:
            slot_key: Non-PHI surrogate (the appeal id) the slot is keyed by.
            timeout: Maximum seconds to wait; ``None`` waits indefinitely.

        Returns:
            An opened :class:`Slot` carrying the backing task ARN in ``backend_ref``.

        Raises:
            CapacityTimeout: If no slot frees before ``timeout``, the gate is closed, or
                a backend (botocore) fault prevents acquisition.
        """
        if self._closed:
            raise CapacityTimeout("concurrency gate is closed")
        loop = asyncio.get_event_loop()
        deadline = None if timeout is None else loop.time() + timeout

        while True:
            # (1) Fast path: reuse a warm task under the lock (no AWS await held).
            async with self._lock:
                if self._closed:
                    raise CapacityTimeout("concurrency gate is closed")
                warm_arn = self._warm.pop() if self._warm else None
                if warm_arn is not None:
                    self._in_use.add(warm_arn)
                    return _slot_for(warm_arn, slot_key)
                may_grow = len(self._in_use) < self._ceiling

            # (2) Slow path: grow the pool. RunTask + poll are done OUTSIDE the lock.
            if may_grow:
                task_arn = await self._launch_task(slot_key=slot_key)
                if task_arn is not None:
                    async with self._lock:
                        self._in_use.add(task_arn)
                    return _slot_for(task_arn, slot_key)
                # RunTask reported quota exhaustion: fall through to wait for a release.

            # (3) At ceiling / quota exhausted: block until a release notifies us.
            remaining = None
            if deadline is not None:
                remaining = deadline - loop.time()
                if remaining <= 0:
                    raise CapacityTimeout(
                        f"no slot available within {timeout}s (capacity={self._ceiling})"
                    )
            try:
                async with self._cond:
                    await asyncio.wait_for(self._cond.wait(), remaining)
            except asyncio.TimeoutError as exc:
                raise CapacityTimeout(
                    f"no slot available within {timeout}s (capacity={self._ceiling})"
                ) from exc

    async def _launch_task(self, *, slot_key: str) -> Optional[str]:
        """Run one task and poll it to ``RUNNING``; return its ARN or ``None``.

        ``None`` signals a ``RESOURCE:FARGATE`` quota-exhaustion failure (the caller then
        applies backpressure). Any botocore fault is remapped to :class:`CapacityTimeout`.
        """
        cfg = self._config
        slot_id = uuid.uuid4().hex
        request: Dict[str, Any] = {
            "cluster": cfg.cluster,
            "taskDefinition": cfg.task_definition,
            "count": 1,
            "capacityProviderStrategy": [
                {"capacityProvider": cfg.capacity_provider, "weight": 1, "base": 0}
            ],
            "platformVersion": "LATEST",
            "networkConfiguration": {
                "awsvpcConfiguration": {
                    "subnets": cfg.subnets,
                    "securityGroups": cfg.security_groups,
                    "assignPublicIp": cfg.assign_public_ip,
                }
            },
            "overrides": {
                "containerOverrides": [
                    {
                        "name": cfg.container_name,
                        "environment": [
                            {"name": "BACKSTOP_SLOT_ID", "value": slot_id},
                            # NON-PHI surrogate key only — never payer/PHI data.
                            {"name": "BACKSTOP_APPEAL_ID", "value": slot_key},
                        ],
                    }
                ]
            },
            "startedBy": _STARTED_BY,
            "clientToken": slot_id,  # idempotency: a retried RunTask won't double-launch.
            "tags": [
                {"key": "app", "value": "backstop"},
                {"key": "pool", "value": "appeal-worker"},
            ],
        }
        try:
            response = await self._client().run_task(**request)
        except Exception as exc:  # - remap any botocore fault to domain.
            raise _backend_timeout("ecs.run_task failed", exc) from exc

        failures = response.get("failures") or []
        for failure in failures:
            reason = str(failure.get("reason", ""))
            if reason.startswith(_QUOTA_EXHAUSTED_PREFIX):
                return None  # Quota exhausted: signal backpressure, do not crash.
            raise _backend_timeout(f"ecs.run_task failure: {reason}", None)

        tasks = response.get("tasks") or []
        if not tasks:
            raise _backend_timeout("ecs.run_task returned no tasks", None)
        task_arn = str(tasks[0]["taskArn"])
        await self._poll_running(task_arn)
        return task_arn

    async def _poll_running(self, task_arn: str) -> None:
        """Poll ``describe_tasks`` with exponential backoff until ``RUNNING``.

        A freshly-launched ARN can briefly 404 (eventual consistency) — a transient
        ``failures`` entry is retried, not treated as terminal. A ``STOPPED`` task is a
        hard failure.
        """
        backoff = _BACKOFF_INITIAL_S
        while True:
            try:
                response = await self._client().describe_tasks(
                    cluster=self._config.cluster, tasks=[task_arn]
                )
            except Exception as exc:  # - remap botocore fault to domain.
                raise _backend_timeout("ecs.describe_tasks failed", exc) from exc

            tasks = response.get("tasks") or []
            if tasks:
                status = str(tasks[0].get("lastStatus", ""))
                if status == _RUNNING:
                    return
                if status == _STOPPED:
                    reason = str(tasks[0].get("stoppedReason", "task stopped"))
                    raise _backend_timeout(f"task stopped before RUNNING: {reason}", None)
            # No task yet (eventual consistency) or still PENDING: back off and retry.
            await self._sleep(backoff)
            backoff = min(backoff * _BACKOFF_FACTOR, _BACKOFF_MAX_S)

    async def release(self, slot: Slot) -> None:
        """Return ``slot`` to the warm pool or ``StopTask`` it; idempotent.

        Up to ``warm_keep`` released tasks are retained warm for instant reuse; the rest
        are stopped. A double-release is a no-op (guarded by a released-id set). After
        updating the pool, a waiting :meth:`acquire` is notified.
        """
        task_arn = slot.backend_ref
        stop = False
        async with self._lock:
            if slot.slot_id in self._released or task_arn is None:
                if slot.slot_id not in self._released:
                    self._released.add(slot.slot_id)
                return
            self._released.add(slot.slot_id)
            self._in_use.discard(task_arn)
            if not self._closed and len(self._warm) < self._config.warm_keep:
                self._warm.add(task_arn)
            else:
                stop = True

        if stop:
            await self._stop_task(task_arn)
        await self._notify_waiter()

    async def _stop_task(self, task_arn: str) -> None:
        try:
            await self._client().stop_task(
                cluster=self._config.cluster,
                task=task_arn,
                reason="backstop: appeal complete",
            )
        except Exception as exc:  # - remap botocore fault to domain.
            raise _backend_timeout("ecs.stop_task failed", exc) from exc

    async def _notify_waiter(self) -> None:
        async with self._cond:
            self._cond.notify(1)

    @asynccontextmanager
    async def slot(self, slot_key: str) -> AsyncIterator[Slot]:
        """Acquire/release a slot as an async context manager.

        Release is guaranteed in ``finally``, so the slot is returned even when the
        wrapped body raises.
        """
        acquired = await self.acquire(slot_key=slot_key)
        try:
            yield acquired
        finally:
            await self.release(acquired)

    # ----------------------------------------------------------------- #
    # Pool management / introspection.
    # ----------------------------------------------------------------- #
    async def ensure_capacity(self, *, target: int) -> int:
        """Pre-warm the pool toward ``target`` ready slots, capped at the ceiling.

        Launches tasks until ``warm + in_use`` reaches ``min(target, max_concurrency)``,
        retaining the new tasks warm. Returns the resulting provisionable capacity.
        """
        want = min(max(target, 0), self._ceiling)
        while True:
            async with self._lock:
                if self._closed:
                    break
                have = len(self._warm) + len(self._in_use)
                if have >= want:
                    break
            task_arn = await self._launch_task(slot_key="prewarm")
            if task_arn is None:
                break  # Quota exhausted: stop growing, report what we can provision.
            async with self._lock:
                self._warm.add(task_arn)

        async with self._lock:
            return min(len(self._warm) + len(self._in_use), self._ceiling) or want

    async def capacity(self) -> CapacitySnapshot:
        """Return a point-in-time capacity snapshot (``mode`` is always ``REAL``)."""
        async with self._lock:
            in_use = len(self._in_use)
        return CapacitySnapshot(
            capacity=self._ceiling,
            in_use=in_use,
            available=self._ceiling - in_use,
            mode=IntegrationMode.REAL,
        )

    async def reconcile(self) -> int:
        """Rebuild the in-use count from live ECS tasks after a restart.

        Pages ``list_tasks(startedBy='backstop-swarm', desiredStatus='RUNNING')`` and
        treats every recovered ``RUNNING`` task as in-use. Returns the reconciled count.
        """
        recovered: Set[str] = set()
        next_token: Optional[str] = None
        while True:
            request: Dict[str, Any] = {
                "cluster": self._config.cluster,
                "startedBy": _STARTED_BY,
                "desiredStatus": _RUNNING,
            }
            if next_token is not None:
                request["nextToken"] = next_token
            try:
                response = await self._client().list_tasks(**request)
            except Exception as exc:  # - remap botocore fault to domain.
                raise _backend_timeout("ecs.list_tasks failed", exc) from exc
            recovered.update(str(arn) for arn in response.get("taskArns", []))
            next_token = response.get("nextToken")
            if not next_token:
                break

        async with self._lock:
            self._in_use = recovered
            self._warm.clear()
            return len(self._in_use)

    async def aclose(self) -> None:
        """Stop accepting new acquires and ``StopTask`` every warm + in-use task."""
        async with self._lock:
            self._closed = True
            to_stop = list(self._warm | self._in_use)
            self._warm.clear()
            self._in_use.clear()
        for task_arn in to_stop:
            await self._stop_task(task_arn)
        await self._notify_waiter()


# --------------------------------------------------------------------------- #
# Module-private helpers.
# --------------------------------------------------------------------------- #
def _slot_for(task_arn: str, slot_key: str) -> Slot:
    """Build a :class:`Slot` keyed by ``slot_key`` and backed by ``task_arn``."""
    return Slot(slot_id=uuid.uuid4().hex, slot_key=slot_key, backend_ref=task_arn)


def _backend_timeout(message: str, cause: Optional[BaseException]) -> CapacityTimeout:
    """Remap a backend (botocore) fault to a domain :class:`CapacityTimeout`.

    The Service layer must never see raw botocore types, so every ECS fault surfaces as
    a domain error. The original cause is preserved for diagnostics.
    """
    detail = f"{message}: {cause}" if cause is not None else message
    return CapacityTimeout(detail)

"""Task supervisor port: tracked spawning and graceful drain of background tasks.

Defines the :class:`TaskSupervisorPort` protocol. Every background coroutine
(swarm call lanes, async pipeline steps) is spawned through this injected
singleton so it is *tracked* -- there is no fire-and-forget. On shutdown the
FastAPI lifespan calls :meth:`drain` (await all within a budget) or
:meth:`cancel_all`, guaranteeing no orphaned task survives process teardown.

Implemented by ``AsyncioTaskSupervisor`` (tracked spawn + drain/cancel). This
module imports only the standard library and performs no I/O beyond asyncio task
management.
"""

from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Protocol, runtime_checkable


@runtime_checkable
class TaskSupervisorPort(Protocol):
    """Lifecycle port that tracks every spawned task for graceful shutdown.

    Services name this protocol and never call ``asyncio.create_task`` directly,
    which would leak untracked (fire-and-forget) tasks.
    """

    def spawn(self, coro: Awaitable[Any], *, name: str) -> asyncio.Task[Any]:
        """Schedule ``coro`` as a tracked task labelled ``name``.

        The returned :class:`asyncio.Task` is retained by the supervisor so it can
        be awaited or cancelled during shutdown. ``name`` is used for structured
        logging and debugging; it is non-PHI metadata.
        """
        ...

    async def drain(self, timeout: float) -> None:
        """Await all tracked tasks, up to ``timeout`` seconds.

        Returns once every tracked task has completed or the budget elapses.
        Tasks still running after ``timeout`` are left for :meth:`cancel_all`.
        """
        ...

    async def cancel_all(self) -> None:
        """Cancel every in-flight tracked task and await their cancellation.

        Used on hard shutdown so no orphaned task survives. Idempotent: calling
        it with no live tasks is a no-op.
        """
        ...

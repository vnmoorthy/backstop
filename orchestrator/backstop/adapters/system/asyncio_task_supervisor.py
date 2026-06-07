"""Tracked asyncio task spawner + graceful drain for :class:`TaskSupervisorPort`.

Every background coroutine is spawned through this injected singleton so it is
*tracked* -- there is no fire-and-forget. On shutdown the FastAPI lifespan calls
:meth:`drain` (await all within a budget) or :meth:`cancel_all`, guaranteeing no
orphaned task survives process teardown. This module imports only the standard
library and performs no I/O beyond asyncio task management.
"""

from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Set

__all__ = ["AsyncioTaskSupervisor"]


class AsyncioTaskSupervisor:
    """Production :class:`~backstop.ports.task_supervisor_port.TaskSupervisorPort`.

    Retains a strong reference to every spawned task until it completes, closing
    the well-known asyncio foot-gun where a task referenced only by a local
    variable is silently garbage-collected mid-flight. Completed tasks remove
    themselves via a done-callback so the tracking set stays bounded.
    """

    def __init__(self) -> None:
        """Start with an empty tracking set."""
        self._tasks: Set[asyncio.Task[Any]] = set()

    def spawn(self, coro: Awaitable[Any], *, name: str) -> asyncio.Task[Any]:
        """Schedule ``coro`` as a tracked task labelled ``name``.

        The returned task is retained by the supervisor so it can be awaited or
        cancelled during shutdown; ``name`` is non-PHI metadata used for
        structured logging.
        """
        task: asyncio.Task[Any] = asyncio.ensure_future(coro)
        task.set_name(name)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    async def drain(self, timeout: float) -> None:
        """Await all tracked tasks, up to ``timeout`` seconds.

        Returns once every tracked task has completed or the budget elapses;
        tasks still running after ``timeout`` are left for :meth:`cancel_all`.
        Exceptions from drained tasks are swallowed here (they are observed by
        the task owner / audit log, not by the supervisor).
        """
        pending = self._snapshot()
        if not pending:
            return
        await asyncio.wait(pending, timeout=timeout)

    async def cancel_all(self) -> None:
        """Cancel every in-flight tracked task and await their cancellation.

        Idempotent: calling it with no live tasks is a no-op. Cancellation
        exceptions are absorbed so shutdown always completes.
        """
        pending = self._snapshot()
        if not pending:
            return
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)

    def _snapshot(self) -> Set[asyncio.Task[Any]]:
        """Return the set of not-yet-finished tracked tasks."""
        return {task for task in self._tasks if not task.done()}

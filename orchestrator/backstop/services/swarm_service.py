"""SwarmService — concurrent, admission-controlled appeal fan-out.

Each appeal's body runs inside ``async with gate.slot(appeal_id)`` so global
concurrency never exceeds the gate cap; the context manager guarantees the slot
is released in ``finally`` even when the body raises. The swarm spawns nothing
fire-and-forget — every lane is awaited as a tracked coroutine — so at the end
of :meth:`run_all`, ``gate.capacity().in_use == 0`` regardless of per-appeal
failures.

The per-appeal body is an injected async callable (the wired PAVO/CallService
loop in production, a fake in tests). The service owns only the admission and
concurrency discipline; it depends on the :class:`ConcurrencyGatePort` alone.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Awaitable, Callable, List, Optional, Sequence, Tuple

from backstop.ports.concurrency_gate_port import ConcurrencyGatePort

__all__ = ["AppealOutcome", "SwarmService"]

#: An appeal body: given an appeal id, run its PAVO loop to completion.
AppealBody = Callable[[str], Awaitable[None]]


@dataclass(frozen=True)
class AppealOutcome:
    """The terminal outcome of one appeal lane.

    Attributes:
        appeal_id: The appeal this lane processed.
        ok: Whether the body completed without raising.
        error: The string form of the raised error, when ``ok`` is ``False``.
    """

    appeal_id: str
    ok: bool
    error: Optional[str] = None


class SwarmService:
    """Run many appeal bodies concurrently under a shared admission gate."""

    def __init__(self, gate: ConcurrencyGatePort) -> None:
        """Store the injected admission gate (the only collaborator)."""
        self._gate = gate

    async def run_all(
        self,
        appeal_ids: Sequence[str],
        body: AppealBody,
    ) -> Tuple[AppealOutcome, ...]:
        """Process every appeal under the gate; never exceed its capacity.

        Each lane acquires a slot before running ``body`` and releases it on the
        way out (even on exception). One failing lane does not cancel the
        others; its failure is captured in its :class:`AppealOutcome`. On
        return, every slot has been released.
        """
        if not appeal_ids:
            return ()

        tasks: List[asyncio.Task[AppealOutcome]] = [
            asyncio.ensure_future(self._lane(appeal_id, body))
            for appeal_id in appeal_ids
        ]
        return tuple(await asyncio.gather(*tasks))

    async def _lane(self, appeal_id: str, body: AppealBody) -> AppealOutcome:
        """Run one appeal body inside a guaranteed-released admission slot."""
        async with self._gate.slot(appeal_id):
            try:
                await body(appeal_id)
            except Exception as exc:  # lane-isolated failure; never cancels peers
                return AppealOutcome(appeal_id=appeal_id, ok=False, error=str(exc))
        return AppealOutcome(appeal_id=appeal_id, ok=True)

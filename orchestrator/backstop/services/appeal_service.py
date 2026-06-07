"""AppealService — create and advance an :class:`Appeal` aggregate.

The entry point of the pipeline: from a parsed :class:`Denial` it computes the
route via the pure routing policy, mints an id through the injected
:class:`IdGenPort`, persists a fresh ``DENIED`` aggregate, and appends the
opening timeline event. Lifecycle advances go through
:meth:`AppealRepositoryPort.update_status_atomic` (optimistic-lock CAS) so a
torn read can never file or double-advance an appeal.

The service depends only on ports + the pure domain (routing policy, money,
aggregate). It never touches the clock or id generation directly.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Dict, Optional

from backstop.domain.carc_table import CarcTable
from backstop.domain.enums import AppealStatus, RouteDecision
from backstop.domain.models import Appeal, Denial
from backstop.domain.money import RecoverableDollars, SolDeadline
from backstop.domain.routing_policy import decide
from backstop.ports.appeal_repository_port import (
    AppealEventRecord,
    AppealRepositoryPort,
)
from backstop.ports.clock_port import ClockPort
from backstop.ports.id_gen_port import IdGenPort

__all__ = ["CreateAppealResult", "AppealService"]


@dataclass(frozen=True)
class CreateAppealResult:
    """The result of creating one appeal.

    Attributes:
        appeal: The persisted ``DENIED`` aggregate.
        route: The route the policy chose for the denial.
    """

    appeal: Appeal
    route: RouteDecision


class AppealService:
    """Create appeals from denials and drive their lifecycle via CAS."""

    def __init__(
        self,
        *,
        repo: AppealRepositoryPort,
        clock: ClockPort,
        id_gen: IdGenPort,
        carc_table: CarcTable,
    ) -> None:
        """Store the repository, clock, id generator, and CARC table."""
        self._repo = repo
        self._clock = clock
        self._id_gen = id_gen
        self._carc_table = carc_table

    async def create(
        self,
        denial: Denial,
        *,
        recoverable: RecoverableDollars,
        sol: SolDeadline,
        needs_human_review: bool = False,
    ) -> CreateAppealResult:
        """Create, route, and persist a fresh ``DENIED`` appeal.

        The route is computed by the pure policy; the aggregate is saved and an
        opening event is appended to its timeline.
        """
        route = decide(denial, self._carc_table)
        appeal_id = self._id_gen.new_id()
        appeal = Appeal(
            id=appeal_id,
            status=AppealStatus.DENIED,
            denial=denial,
            recoverable=recoverable,
            sol=sol,
            route=route,
            needs_human_review=needs_human_review,
            events=(),
        )
        await self._repo.save(appeal)
        await self._repo.append_event(
            appeal_id,
            self._event(appeal_id, seq=0, kind="denial_received"),
        )
        return CreateAppealResult(appeal=appeal, route=route)

    async def advance(
        self,
        appeal_id: str,
        *,
        expected: AppealStatus,
        new: AppealStatus,
        event_kind: str,
        seq: int,
    ) -> bool:
        """Atomically move ``appeal_id`` from ``expected`` to ``new`` and log it.

        Returns ``True`` when the CAS won (and the event was appended); ``False``
        on a stale read, in which case no event is written.
        """
        won = await self._repo.update_status_atomic(appeal_id, expected, new)
        if not won:
            return False
        await self._repo.append_event(
            appeal_id,
            self._event(appeal_id, seq=seq, kind=event_kind),
        )
        return True

    async def get(self, appeal_id: str) -> Appeal:
        """Load the appeal aggregate (raises ``AppealNotFound`` if unknown)."""
        return await self._repo.load(appeal_id)

    def _event(
        self,
        appeal_id: str,
        *,
        seq: int,
        kind: str,
        payload: Optional[Dict[str, str]] = None,
    ) -> AppealEventRecord:
        """Build a clock-stamped, already-redacted timeline event record."""
        return AppealEventRecord(
            appeal_id=appeal_id,
            seq=seq,
            kind=kind,
            ts_iso=self._clock.now().isoformat(),
            payload_json=json.dumps(payload or {}, separators=(",", ":")),
        )

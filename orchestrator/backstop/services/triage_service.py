"""TriageService — rank the worklist by recoverable-$ x SOL urgency.

Builds the nurse worklist by scoring every appeal with the pure
:func:`backstop.domain.triage.score` (recoverable dollars weighted by
statute-of-limitations urgency) and returning them sorted most-urgent first.
``now`` is read through the injected :class:`ClockPort` so ordering is
deterministic in tests. The service performs no mutation; it loads pages of
appeals through the :class:`AppealRepositoryPort` and ranks them.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

from backstop.domain.models import Appeal
from backstop.domain.triage import TriageScore, score
from backstop.ports.appeal_repository_port import (
    AppealFilter,
    AppealRepositoryPort,
)
from backstop.ports.clock_port import ClockPort

__all__ = ["TriageItem", "TriageService"]


@dataclass(frozen=True)
class TriageItem:
    """One ranked worklist row.

    Attributes:
        appeal: The appeal aggregate being ranked.
        score: Its deterministic triage score (value + components).
    """

    appeal: Appeal
    score: TriageScore


class TriageService:
    """Score and order appeals into a most-urgent-first worklist."""

    def __init__(
        self,
        repo: AppealRepositoryPort,
        clock: ClockPort,
    ) -> None:
        """Store the injected repository and clock ports."""
        self._repo = repo
        self._clock = clock

    def rank(self, appeals: Sequence[Appeal]) -> Tuple[TriageItem, ...]:
        """Score ``appeals`` and return them most-urgent (highest value) first.

        Ties on the composite score fall back to the appeal id for a stable,
        deterministic ordering.
        """
        now = self._clock.now().date()
        items: List[TriageItem] = [
            TriageItem(appeal=appeal, score=score(appeal, now))
            for appeal in appeals
        ]
        items.sort(
            key=lambda item: (item.score.value, item.appeal.id),
            reverse=True,
        )
        return tuple(items)

    async def worklist(
        self,
        *,
        filter: Optional[AppealFilter] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> Tuple[TriageItem, ...]:
        """Load a filtered page of appeals and return it ranked by urgency."""
        page = await self._repo.list(
            filter if filter is not None else AppealFilter(),
            limit=limit,
            offset=offset,
        )
        return self.rank(page.items)

"""Pure triage scoring for appeals.

:func:`score` ranks an :class:`~backstop.domain.models.Appeal` by combining how
much money is recoverable with how urgent its statute-of-limitations deadline
is, producing a deterministic :class:`TriageScore`. Higher scores demand sooner
attention. The function is pure: ``now`` is injected, so there is no clock
access, and there is no I/O or vendor dependency.

Urgency rises as the SOL deadline approaches and saturates once it is within a
short fuse window; an already-expired deadline yields maximum urgency so the
case surfaces for an explicit abandon/write-off decision rather than silently
sinking.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from backstop.domain.models import Appeal

__all__ = ["TriageScore", "URGENCY_FUSE_DAYS", "URGENCY_HORIZON_DAYS", "score"]


# Within this many days of the deadline, urgency is pinned at its maximum.
URGENCY_FUSE_DAYS: int = 3

# Beyond this many days out, urgency is at its minimum (long runway).
URGENCY_HORIZON_DAYS: int = 90


@dataclass(frozen=True, order=True)
class TriageScore:
    """A deterministic triage ranking for one appeal.

    Ordering is by ``value`` (the field declared first), so a list of
    ``TriageScore`` sorts from least to most urgent; reverse for a worklist.
    The component ``recoverable_cents`` and ``urgency`` are retained for
    explainability in the dashboard.
    """

    value: float
    recoverable_cents: int
    urgency: float


def _urgency(days_remaining: int) -> float:
    """Map days-until-deadline to an urgency multiplier in ``[0.0, 1.0]``.

    * ``<= URGENCY_FUSE_DAYS`` (including past-due): ``1.0``.
    * ``>= URGENCY_HORIZON_DAYS``: a small non-zero floor so value still ranks.
    * In between: linear ramp from the horizon down to the fuse.
    """
    floor = 0.05
    if days_remaining <= URGENCY_FUSE_DAYS:
        return 1.0
    if days_remaining >= URGENCY_HORIZON_DAYS:
        return floor
    span = URGENCY_HORIZON_DAYS - URGENCY_FUSE_DAYS
    progressed = URGENCY_HORIZON_DAYS - days_remaining
    ramped = progressed / span
    return floor + (1.0 - floor) * ramped


def score(appeal: Appeal, now: date) -> TriageScore:
    """Compute the triage score for ``appeal`` as of ``now``.

    The score is ``recoverable_dollars * urgency`` where dollars are derived
    from exact cents and urgency comes from the SOL deadline. Both component
    values are preserved on the returned :class:`TriageScore` for audit/UI.

    Args:
        appeal: The aggregate to rank.
        now: The reference date used to compute deadline urgency.

    Returns:
        A :class:`TriageScore`; larger ``value`` means higher priority.
    """
    recoverable_cents = appeal.recoverable.cents
    days_remaining = appeal.sol.days_remaining(now)
    urgency = _urgency(days_remaining)
    value = (recoverable_cents / 100.0) * urgency
    return TriageScore(
        value=value,
        recoverable_cents=recoverable_cents,
        urgency=urgency,
    )

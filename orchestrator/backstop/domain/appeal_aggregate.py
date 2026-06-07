"""Pure state-machine transitions for the :class:`Appeal` aggregate.

This module owns the legal lifecycle of an appeal. :func:`apply` is a pure
function ``(appeal, event) -> appeal``: it validates the event against the
current :class:`~backstop.domain.enums.AppealStatus`, appends the event to the
immutable history, and returns a *new* :class:`~backstop.domain.models.Appeal`.

The load-bearing invariant: an appeal can reach ``FILED`` **only** via a
:class:`~backstop.domain.models.SignedEvent`. No plain event, and no sequence
of events, can file an appeal without a nurse signature. Illegal transitions
raise :class:`~backstop.domain.errors.AppealStateError`. No I/O, no clock, no
vendor imports.
"""

from __future__ import annotations

import dataclasses
from typing import Dict, FrozenSet

from backstop.domain.enums import AppealStatus
from backstop.domain.errors import AppealStateError
from backstop.domain.models import Appeal, AppealEvent, SignedEvent

__all__ = ["TERMINAL_STATES", "allowed_targets", "apply"]


# Terminal states accept no further transitions.
TERMINAL_STATES: FrozenSet[AppealStatus] = frozenset(
    {AppealStatus.FILED, AppealStatus.ABANDONED}
)

# Adjacency of the lifecycle graph (excluding the universal ABANDONED edge,
# which is added in ``allowed_targets`` for every non-terminal state).
_FORWARD_EDGES: Dict[AppealStatus, FrozenSet[AppealStatus]] = {
    AppealStatus.DENIED: frozenset({AppealStatus.TRIAGED}),
    AppealStatus.TRIAGED: frozenset({AppealStatus.IN_APPEAL}),
    AppealStatus.IN_APPEAL: frozenset({AppealStatus.AWAITING_SIGNOFF}),
    AppealStatus.AWAITING_SIGNOFF: frozenset({AppealStatus.FILED}),
    AppealStatus.FILED: frozenset(),
    AppealStatus.ABANDONED: frozenset(),
}


def allowed_targets(status: AppealStatus) -> FrozenSet[AppealStatus]:
    """Return the statuses reachable in one step from ``status``.

    Every non-terminal state may additionally move to ``ABANDONED``.
    """
    if status in TERMINAL_STATES:
        return frozenset()
    return _FORWARD_EDGES[status] | frozenset({AppealStatus.ABANDONED})


def _target_for(event: AppealEvent, current: AppealStatus) -> AppealStatus:
    """Resolve the intended target status for ``event`` from ``current``.

    The mapping is by event ``kind`` so callers describe *what happened*
    (``"triaged"``, ``"signed"``, ...) and the aggregate decides the resulting
    status. ``SignedEvent`` is special-cased to target ``FILED``.
    """
    if isinstance(event, SignedEvent):
        return AppealStatus.FILED

    kind = event.kind
    by_kind: Dict[str, AppealStatus] = {
        "triaged": AppealStatus.TRIAGED,
        "denial_parsed": AppealStatus.TRIAGED,
        "appeal_opened": AppealStatus.IN_APPEAL,
        "turn_routed": AppealStatus.IN_APPEAL,
        "letter_drafted": AppealStatus.AWAITING_SIGNOFF,
        "awaiting_signoff": AppealStatus.AWAITING_SIGNOFF,
        "abandoned": AppealStatus.ABANDONED,
    }
    if kind not in by_kind:
        raise AppealStateError(
            f"unknown event kind {kind!r}",
            from_status=current.value,
            event=kind,
        )
    return by_kind[kind]


def apply(appeal: Appeal, event: AppealEvent) -> Appeal:
    """Apply ``event`` to ``appeal`` and return the resulting new aggregate.

    The function is pure and total over its declared inputs:

    * Rejects any event applied to a terminal appeal.
    * Resolves the event's target status and rejects it if that target is not
      reachable from the current status.
    * Enforces that ``FILED`` is reachable **only** through a
      :class:`~backstop.domain.models.SignedEvent`.
    * Appends the event to the immutable ``events`` history (with a monotonic
      ``seq`` if the event left it at ``0``) and updates ``status``.

    Args:
        appeal: The current aggregate snapshot.
        event: The fact to apply.

    Returns:
        A new :class:`~backstop.domain.models.Appeal` reflecting the transition.

    Raises:
        AppealStateError: If the transition is illegal or unsigned-to-FILED.
    """
    current = appeal.status

    if current in TERMINAL_STATES:
        raise AppealStateError(
            f"appeal in terminal state {current.value} accepts no events",
            from_status=current.value,
            event=event.kind,
        )

    target = _target_for(event, current)

    if target not in allowed_targets(current):
        raise AppealStateError(
            f"illegal transition {current.value} -> {target.value}",
            from_status=current.value,
            event=event.kind,
        )

    # The central honesty invariant: filing requires a signature event.
    if target is AppealStatus.FILED and not isinstance(event, SignedEvent):
        raise AppealStateError(
            "cannot reach FILED without a SignedEvent",
            from_status=current.value,
            event=event.kind,
        )

    next_seq = len(appeal.events)
    stamped = event if event.seq == next_seq else dataclasses.replace(
        event, seq=next_seq
    )

    return dataclasses.replace(
        appeal,
        status=target,
        events=(*appeal.events, stamped),
    )

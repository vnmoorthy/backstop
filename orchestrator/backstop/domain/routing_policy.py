"""Pure routing policy: denial -> :class:`RouteDecision`.

Given a :class:`~backstop.domain.models.Denial`, :func:`decide` selects how the
swarm should respond. The decision starts from the CARC's ``default_route`` in
the :class:`~backstop.domain.carc_table.CarcTable` and then applies a small set
of deterministic RARC overrides (e.g. a "missing information" remark steers a
default appeal toward a resubmission instead).

The function is pure: the table is injected, ``now`` is not consulted, and
there is no I/O or vendor dependency. An unknown CARC falls back to a
conservative, human-review-friendly default.
"""

from __future__ import annotations

from typing import FrozenSet

from backstop.domain.carc_table import CarcTable
from backstop.domain.enums import RouteDecision
from backstop.domain.models import Denial

__all__ = ["FALLBACK_ROUTE", "decide"]


# When the CARC is not in the table we cannot justify an automated appeal, so we
# resubmit (the cheapest reversible action) and let downstream review escalate.
FALLBACK_ROUTE: RouteDecision = RouteDecision.RESUBMIT

# RARC remark codes that indicate the claim is missing/incomplete data; for an
# otherwise appealable denial these favour a clean resubmission first.
_MISSING_INFO_RARCS: FrozenSet[str] = frozenset({"N01", "N04", "N657", "MA130"})

# RARC remark codes that indicate a medical-necessity dispute; these escalate an
# ordinary appeal to a peer-to-peer clinical review.
_PEER_TO_PEER_RARCS: FrozenSet[str] = frozenset({"N115", "M127"})


def decide(denial: Denial, table: CarcTable) -> RouteDecision:
    """Return the route for ``denial`` using ``table`` plus RARC overrides.

    Resolution order:

    1. Look up the CARC (``denial.denial_code``). If unknown, return
       :data:`FALLBACK_ROUTE`.
    2. Take the entry's ``default_route``.
    3. Apply RARC overrides when the base route is ``APPEAL``: a missing-info
       remark becomes ``RESUBMIT``; a medical-necessity remark becomes
       ``PEER_TO_PEER``.

    Args:
        denial: The denial to route.
        table: The loaded CARC/RARC lookup table.

    Returns:
        The chosen :class:`RouteDecision`.
    """
    entry = table.get(denial.denial_code)
    if entry is None:
        return FALLBACK_ROUTE

    route = entry.default_route
    rarc = denial.rarc

    if route is RouteDecision.APPEAL and rarc is not None:
        if rarc in _MISSING_INFO_RARCS:
            return RouteDecision.RESUBMIT
        if rarc in _PEER_TO_PEER_RARCS:
            return RouteDecision.PEER_TO_PEER

    return route

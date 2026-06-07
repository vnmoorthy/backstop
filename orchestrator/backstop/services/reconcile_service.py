"""ReconcileService — cross-desk contradiction detection.

When the swarm calls several payer desks about the same claim, the desks can
report mutually-inconsistent denial reasons. This service collects the
per-desk findings (a denial CARC code reported by a specialist line) and detects
a *contradiction*: two desks that cannot both be telling the truth about the
same claim.

The canonical contradiction this targets is the prior-auth / coverage / missing
-info triangle:

* ``CO-197`` — "precertification/authorization absent" (PRIOR_AUTH_DESK), versus
* ``CO-50`` — "not medically necessary / non-covered" (PROVIDER_LINE), versus
* ``CO-16`` — "claim lacks information" (BILLING_OFFICE).

If one desk says the service needs prior auth while another says it is simply
non-covered and a third says the claim is merely missing data, the denials are
incompatible and the appeal has leverage: the payer cannot consistently hold all
three positions. The service is pure decisioning over non-PHI findings and has
no port dependency — it is injected into the swarm post-call step.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, FrozenSet, List, Optional, Sequence, Set, Tuple

from backstop.domain.enums import SpecialistKind

__all__ = ["DeskFinding", "Contradiction", "ReconcileService"]


def _norm(code: str) -> str:
    """Normalize a CARC code to bare digits (``"CO-197"`` -> ``"197"``)."""
    return code.strip().upper().removeprefix("CO-").removeprefix("CO").lstrip("-")


# A CARC normalized to its bare number, grouped by the assertion it makes about
# the claim. Two findings whose groups differ are mutually contradictory.
_PRIOR_AUTH_CODES: FrozenSet[str] = frozenset({"197", "198", "15"})
_NON_COVERED_CODES: FrozenSet[str] = frozenset({"50", "96", "204"})
_MISSING_INFO_CODES: FrozenSet[str] = frozenset({"16", "125", "226"})


def _claim_group(code: str) -> Optional[str]:
    """Return the contradiction-group label for ``code`` (or ``None``)."""
    bare = _norm(code)
    if bare in _PRIOR_AUTH_CODES:
        return "prior_auth"
    if bare in _NON_COVERED_CODES:
        return "non_covered"
    if bare in _MISSING_INFO_CODES:
        return "missing_info"
    return None


@dataclass(frozen=True)
class DeskFinding:
    """One desk's reported denial reason for a claim.

    Attributes:
        desk: The specialist line that reported this finding.
        carc: The CARC code the desk gave (e.g. ``"CO-197"`` or ``"197"``).
        note: Optional non-PHI free-text note for the timeline.
    """

    desk: SpecialistKind
    carc: str
    note: str = ""


@dataclass(frozen=True)
class Contradiction:
    """A detected cross-desk inconsistency about one claim.

    Attributes:
        found: Whether a contradiction was detected.
        groups: The distinct claim-assertion groups in conflict, sorted.
        findings: The findings that participate in the contradiction.
        summary: A short, non-PHI human-readable explanation.
    """

    found: bool
    groups: Tuple[str, ...] = ()
    findings: Tuple[DeskFinding, ...] = ()
    summary: str = ""


class ReconcileService:
    """Detect mutually-inconsistent denial reasons across payer desks."""

    def find_contradiction(
        self, findings: Sequence[DeskFinding]
    ) -> Contradiction:
        """Return whether ``findings`` contain a cross-desk contradiction.

        A contradiction exists when at least two findings map to *different*
        assertion groups (prior-auth vs non-covered vs missing-info): the payer
        cannot consistently hold those positions about the same claim at once.
        """
        by_group: Dict[str, List[DeskFinding]] = {}
        for finding in findings:
            group = _claim_group(finding.carc)
            if group is None:
                continue
            by_group.setdefault(group, []).append(finding)

        groups: Set[str] = set(by_group)
        if len(groups) < 2:
            return Contradiction(found=False)

        ordered = tuple(sorted(groups))
        participating: List[DeskFinding] = []
        for group in ordered:
            participating.extend(by_group[group])

        return Contradiction(
            found=True,
            groups=ordered,
            findings=tuple(participating),
            summary=self._summarize(ordered, by_group),
        )

    @staticmethod
    def _summarize(
        groups: Tuple[str, ...],
        by_group: Dict[str, List[DeskFinding]],
    ) -> str:
        """Build a non-PHI explanation naming the conflicting desks/codes."""
        clauses: List[str] = []
        for group in groups:
            desks = ", ".join(
                sorted(f.desk.value for f in by_group[group])
            )
            codes = ", ".join(
                sorted({_norm(f.carc) for f in by_group[group]})
            )
            clauses.append(f"{group} (CARC {codes} via {desks})")
        return "cross-desk contradiction: " + " vs ".join(clauses)

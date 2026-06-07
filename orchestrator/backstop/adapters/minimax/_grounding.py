"""Pure grounding guardrails shared by both ReasoningPort adapters.

This module holds the *contract-enforcing* helpers that make the real MiniMax
adapter and the local simulation behave identically at the port boundary,
regardless of how each one produced its candidate answer:

* :func:`enforce_word_cap` truncates a composed line to ``max_words``.
* :func:`subset_citations` drops any citation id that was not actually supplied
  in the request evidence — the no-fabrication invariant. A model (or a buggy
  template) can never cite an id the caller did not provide.
* :func:`safe_fallback_line` is the single deterministic, evidence-free line
  emitted when grounding is insufficient or the backend refuses.
* :func:`coerce_dialog_act` / :func:`coerce_route` map free-text labels back
  onto the closed domain enums, so an out-of-vocabulary label from a vendor can
  never escape the adapter.

Everything here is pure: standard library only, no I/O, no vendor imports, and
no construction of :class:`RedactedText` (callers mint that through the
redaction port). The functions operate on plain strings and tuples so they are
trivially testable and reusable from either adapter.
"""

from __future__ import annotations

from typing import Iterable, Optional, Tuple

from backstop.domain.enums import DialogAct, RouteDecision

__all__ = [
    "SAFE_FALLBACK_TEXT",
    "GROUNDING_THRESHOLD",
    "enforce_word_cap",
    "subset_citations",
    "safe_fallback_line",
    "coerce_dialog_act",
    "coerce_route",
]

# The exact deterministic line emitted when there is not enough grounded
# evidence to compose a real utterance. Phrased so it is always audit-safe and
# never asserts a clinical or policy fact. Both adapters return this verbatim so
# the "ungrounded -> safe fallback" contract is identical across them.
SAFE_FALLBACK_TEXT: str = (
    "I want to make sure I cite the correct policy "
    "— can you give me the specific reason code on file?"
)

# Minimum normalized match score (in [0, 1]) below which the sim adapter treats
# the evidence as insufficient and falls back. Kept here so the threshold is a
# single shared constant rather than a magic number buried in the adapter.
GROUNDING_THRESHOLD: float = 0.05


def enforce_word_cap(text: str, max_words: int) -> str:
    """Return ``text`` truncated to at most ``max_words`` whitespace tokens.

    Args:
        text: The candidate line.
        max_words: Hard cap on the number of words (``<= 0`` yields an empty
            string).

    Returns:
        The original text when already within the cap, otherwise the first
        ``max_words`` words joined by single spaces. Truncation collapses any
        runs of internal whitespace.
    """
    words = text.split()
    if max_words <= 0:
        return ""
    if len(words) <= max_words:
        return " ".join(words)
    return " ".join(words[:max_words])


def subset_citations(
    cited: Iterable[str], allowed: Iterable[str]
) -> Tuple[str, ...]:
    """Filter ``cited`` ids down to those present in ``allowed``.

    Preserves the order of first appearance in ``cited`` and de-duplicates, so
    the returned tuple is always a deterministic subset of the supplied
    evidence ids. This is the structural guarantee that no adapter can fabricate
    a citation: an id the caller never provided is silently dropped.

    Args:
        cited: Candidate citation ids proposed by the reasoner.
        allowed: The only ids that may legitimately be cited (the request's
            evidence snippet ids).

    Returns:
        A tuple of unique ids, each of which appears in ``allowed``.
    """
    allowed_set = set(allowed)
    seen: set[str] = set()
    out: list[str] = []
    for cid in cited:
        if cid in allowed_set and cid not in seen:
            seen.add(cid)
            out.append(cid)
    return tuple(out)


def safe_fallback_line(max_words: int) -> str:
    """Return the deterministic safe-fallback line within ``max_words``."""
    return enforce_word_cap(SAFE_FALLBACK_TEXT, max_words)


def coerce_dialog_act(
    label: Optional[str], default: DialogAct = DialogAct.REQUEST_INFO
) -> DialogAct:
    """Map a free-text dialog-act ``label`` onto the closed :class:`DialogAct`.

    Accepts either an exact enum value/name (case-insensitive) or a small set
    of natural-language aliases the backend or template table might emit (e.g.
    ``"cite_policy"`` -> :attr:`DialogAct.CITE_POLICY`,
    ``"request_reference_number"`` -> :attr:`DialogAct.REQUEST_INFO`). Anything
    unrecognized falls back to ``default`` so an out-of-vocabulary label can
    never escape the adapter.
    """
    if label is None:
        return default
    key = label.strip().upper().replace("-", "_").replace(" ", "_")
    direct = _DIALOG_ACT_BY_NAME.get(key)
    if direct is not None:
        return direct
    return _DIALOG_ACT_ALIASES.get(key, default)


def coerce_route(
    label: Optional[str], default: RouteDecision = RouteDecision.APPEAL
) -> RouteDecision:
    """Map a free-text route ``label`` onto the closed :class:`RouteDecision`.

    Recognizes the enum values/names plus the sponsor-spec aliases
    (``"peer_to_peer"``, ``"request_records"``). Unknown labels fall back to
    ``default`` so a route value outside the domain enum can never leak out.
    """
    if label is None:
        return default
    key = label.strip().upper().replace("-", "_").replace(" ", "_")
    direct = _ROUTE_BY_NAME.get(key)
    if direct is not None:
        return direct
    return _ROUTE_ALIASES.get(key, default)


# Reverse lookups built once at import time (members are stable, closed sets).
_DIALOG_ACT_BY_NAME = {member.name: member for member in DialogAct}
_ROUTE_BY_NAME = {member.name: member for member in RouteDecision}

# Natural-language aliases the spec's dialog-act vocabulary uses for the live
# call ("state_contradiction", "request_reference_number", ...).
_DIALOG_ACT_ALIASES = {
    "STATE_CONTRADICTION": DialogAct.REBUT,
    "CONTRADICTION": DialogAct.REBUT,
    "REBUTTAL": DialogAct.REBUT,
    "REQUEST_REFERENCE_NUMBER": DialogAct.REQUEST_INFO,
    "ASK_CLARIFICATION": DialogAct.REQUEST_INFO,
    "ASK_FOR_NUMBER": DialogAct.REQUEST_INFO,
    "CITE": DialogAct.CITE_POLICY,
    "POLICY": DialogAct.CITE_POLICY,
    "PROVIDE": DialogAct.PROVIDE_INFO,
    "PURPOSE": DialogAct.STATE_PURPOSE,
    "HANGUP": DialogAct.CLOSE,
    "GOODBYE": DialogAct.CLOSE,
}

# Aliases for the recommended-route vocabulary from the sponsor spec.
_ROUTE_ALIASES = {
    "PEER_TO_PEER": RouteDecision.PEER_TO_PEER,
    "P2P": RouteDecision.PEER_TO_PEER,
    "REQUEST_RECORDS": RouteDecision.APPEAL,
    "RECORDS": RouteDecision.APPEAL,
    "RESUBMISSION": RouteDecision.RESUBMIT,
    "REBILL": RouteDecision.RESUBMIT,
    "WRITEOFF": RouteDecision.WRITE_OFF,
    "WRITE_OFF": RouteDecision.WRITE_OFF,
    "NO_ACTION": RouteDecision.WRITE_OFF,
}

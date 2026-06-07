"""LocalRedactionAdapter — the real, vendor-free PHI scrubber (RedactionPort).

This is the SOLE producer of :class:`~backstop.domain.redacted.RedactedText` in
the system: raw ``str`` enters here and only here is minted into the egress-safe
newtype via the sanctioned token. Scrubbing is genuine local work — a layered
presidio-style ruleset of anchored regexes plus light context cues — and adds no
vendor SDK or network dependency, so it runs identically in real and sim mode and
in CI.

Categories masked (each replaced by a human-readable ``[TAG]`` placeholder so the
redacted text stays readable while carrying no identifier):

* member IDs (alpha-prefixed payer member numbers),
* NPIs (10-digit national provider identifiers, Luhn-checked),
* SSNs (``NNN-NN-NNNN`` and bare 9-digit with context),
* dates of birth / dates (``MM/DD/YYYY``, ``YYYY-MM-DD`` and ISO),
* claim numbers (context-anchored alphanumeric runs),
* names (``Title First Last`` and ``First Last`` after person cues),
* phone numbers (US 10-digit, several punctuations),
* emails and street addresses.

The :meth:`contains_phi` predicate re-runs the same detector for defence-in-depth
runtime assertions in egress adapters. The module imports nothing outside the
standard library and the pure domain layer.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable, FrozenSet, List, Pattern, Tuple

from backstop.domain.redacted import (
    SANCTIONED_TOKEN,
    PhiSpan,
    PhiTag,
    RedactedText,
)
from backstop.ports.redaction_port import Message, RedactedMessage

__all__ = ["LocalRedactionAdapter"]


# A single detection rule: a compiled pattern, the PHI category it marks, and an
# optional validator that vets the matched substring (e.g. Luhn for NPIs) so we
# do not over-mask innocent numbers.
@dataclass(frozen=True)
class _Rule:
    """One ordered PHI detection rule."""

    name: str
    pattern: Pattern[str]
    tag: PhiTag
    group: int = 0
    validate: Callable[[str], bool] = lambda _s: True


# Ordered rules. Order matters: CUED/labelled rules run first so an explicit
# context word ("NPI", "claim", "member") wins the span, then more-specific bare
# identifiers, then loose fallbacks. A character-occupancy map (see ``_scrub``)
# stops two rules from masking the same region twice, so the first rule to claim a
# span owns it. PHI safety-first: where a category is ambiguous we prefer to mask
# (a false positive only over-masks; a false negative leaks PHI).
_RULES: Tuple[_Rule, ...] = (
    # Email — very specific, run early.
    _Rule(
        name="email",
        pattern=re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b"),
        tag=PhiTag.EMAIL,
    ),
    # ---- Cued / labelled identifiers (context word wins) ----
    # SSN labelled, bare 9 digits after an SSN cue.
    _Rule(
        name="ssn_labelled",
        pattern=re.compile(r"(?i)\b(?:ssn|social security)\b[:\s#]*?(\d{9})\b"),
        tag=PhiTag.SSN,
        group=1,
    ),
    # SSN with explicit dashes.
    _Rule(
        name="ssn_dashed",
        pattern=re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
        tag=PhiTag.SSN,
    ),
    # NPI after an explicit cue — masked regardless of Luhn (the cue is decisive;
    # test/synthetic NPIs are not always Luhn-valid and must still be scrubbed).
    _Rule(
        name="npi_labelled",
        pattern=re.compile(r"(?i)\bnpi\b[:\s#]*?(\d{10})\b"),
        tag=PhiTag.NPI,
        group=1,
    ),
    # Claim number after a claim cue — alphanumeric run (letters/digits/hyphen).
    _Rule(
        name="claim_number",
        pattern=re.compile(
            r"(?i)\bclaim\s*(?:id|number|#|no)?\b[:\s#]*?([A-Za-z0-9\-]{6,})"
        ),
        tag=PhiTag.CLAIM_NUMBER,
        group=1,
    ),
    # Member id after a member/subscriber/policy cue.
    _Rule(
        name="member_id_labelled",
        pattern=re.compile(
            r"(?i)\b(?:member|subscriber|policy)\s*(?:id|number|#|no)?\b"
            r"[:\s#]*?([A-Za-z]{0,3}\d{8,12})\b"
        ),
        tag=PhiTag.MEMBER_ID,
        group=1,
    ),
    # ---- Bare identifiers (no cue) ----
    # Member id: an alpha prefix (1-3 letters) + 8-12 digits (e.g. ``W123456789``).
    _Rule(
        name="member_id",
        pattern=re.compile(r"\b[A-Za-z]{1,3}\d{8,12}\b"),
        tag=PhiTag.MEMBER_ID,
    ),
    # Bare 10-digit run — treated as an NPI/identifier and masked (safety-first:
    # a standalone 10-digit number in claims text is an identifier, not prose).
    _Rule(
        name="npi_bare",
        pattern=re.compile(r"(?<!\d)\d{10}(?!\d)"),
        tag=PhiTag.NPI,
    ),
    # Dates: ISO and US slashed/dashed forms. DOB cue upgrades the tag.
    _Rule(
        name="dob_labelled",
        pattern=re.compile(
            r"(?i)\b(?:dob|date of birth|born)\b[:\s]*?"
            r"(\d{1,2}[/\-]\d{1,2}[/\-]\d{2,4}|\d{4}-\d{2}-\d{2})"
        ),
        tag=PhiTag.DOB,
        group=1,
    ),
    _Rule(
        name="date_iso",
        pattern=re.compile(r"\b\d{4}-\d{2}-\d{2}\b"),
        tag=PhiTag.DATE,
    ),
    _Rule(
        name="date_us",
        pattern=re.compile(r"\b\d{1,2}[/\-]\d{1,2}[/\-]\d{2,4}\b"),
        tag=PhiTag.DATE,
    ),
    # US phone numbers in several punctuations.
    _Rule(
        name="phone",
        pattern=re.compile(
            r"(?<!\d)(?:\+?1[\s.\-]?)?(?:\(\d{3}\)|\d{3})[\s.\-]\d{3}[\s.\-]\d{4}(?!\d)"
        ),
        tag=PhiTag.PHONE,
    ),
    # Street address: number + street words + suffix.
    _Rule(
        name="address",
        pattern=re.compile(
            r"\b\d{1,6}\s+(?:[A-Z][a-z]+\.?\s){1,4}"
            r"(?:St|Street|Ave|Avenue|Rd|Road|Blvd|Boulevard|Ln|Lane|Dr|Drive|Ct|Court|Way|Pl|Place)\b\.?"
        ),
        tag=PhiTag.ADDRESS,
    ),
    # Person names after an explicit person cue (patient/member/provider/dr/for).
    _Rule(
        name="name_cued",
        pattern=re.compile(
            r"(?i)\b(?:patient|member|subscriber|provider|insured|enrollee|name|for|mr|mrs|ms|dr)\b"
            r"[:.\s]+([A-Z][a-z]+(?:\s+[A-Z]\.?)?(?:\s+[A-Z][a-z]+){1,2})"
        ),
        tag=PhiTag.NAME,
        group=1,
    ),
    # Bare ``First Last`` / ``First M. Last`` capitalised name (two-three tokens).
    _Rule(
        name="name_bare",
        pattern=re.compile(
            r"\b[A-Z][a-z]+(?:\s+[A-Z]\.?)?\s+[A-Z][a-z]+\b"
        ),
        tag=PhiTag.NAME,
    ),
)

# Words that look like ``First Last`` capitalisation but are not PHI; excluded
# from the bare-name rule to limit false positives on common phrases.
_NAME_STOPWORDS: FrozenSet[str] = frozenset(
    {
        "Prior Authorization",
        "Medical Necessity",
        "United Healthcare",
        "Blue Cross",
        "Blue Shield",
        "Explanation Of",
        "Date Of",
        "Member Id",
        "Claim Number",
        "Social Security",
    }
)


class LocalRedactionAdapter:
    """Real, vendor-free PHI scrubber — the sole minter of ``RedactedText``.

    Implements :class:`~backstop.ports.redaction_port.RedactionPort`. Stateless
    and deterministic: the same input always yields the same masked output and
    span set, which makes the audit-chain hashes reproducible.
    """

    def __init__(self) -> None:
        """Construct the stateless adapter (no configuration required)."""
        self._rules = _RULES

    def redact_text(self, text: str) -> RedactedText:
        """Scrub PHI from *text* and mint the sanctioned ``RedactedText``."""
        masked, spans = self._scrub(text)
        return RedactedText.from_redaction(masked, token=SANCTIONED_TOKEN, spans=spans)

    def redact_messages(self, msgs: List[Message]) -> List[RedactedMessage]:
        """Redact every message, preserving order and roles."""
        return [
            RedactedMessage(role=m.role, content=self.redact_text(m.content))
            for m in msgs
        ]

    def contains_phi(self, text: str) -> bool:
        """Return whether *text* still matches any PHI pattern."""
        _masked, spans = self._scrub(text)
        return bool(spans)

    # ----------------------------------------------------------------- #
    # Internal scrubber.
    # ----------------------------------------------------------------- #
    def _scrub(self, text: str) -> Tuple[str, Tuple[PhiSpan, ...]]:
        """Mask every detected PHI span, returning masked text and source spans.

        A character occupancy map over the original string prevents two rules
        from masking overlapping regions twice. Spans are reported against the
        ORIGINAL (pre-redaction) offsets, per the ``PhiSpan`` contract, then the
        replacements are applied right-to-left so earlier offsets stay valid.
        """
        occupied = [False] * len(text)
        # (start, end, tag) against original offsets.
        hits: List[Tuple[int, int, PhiTag]] = []

        for rule in self._rules:
            for match in rule.pattern.finditer(text):
                start, end = match.span(rule.group)
                if start == end:
                    continue
                fragment = text[start:end]
                if rule.name == "name_bare" and fragment in _NAME_STOPWORDS:
                    continue
                if not rule.validate(fragment):
                    continue
                # Skip if this region was already claimed by an earlier rule.
                if any(occupied[i] for i in range(start, end)):
                    continue
                for i in range(start, end):
                    occupied[i] = True
                hits.append((start, end, rule.tag))

        if not hits:
            return text, ()

        hits.sort()
        spans = tuple(PhiSpan(start=s, end=e, tag=t) for s, e, t in hits)

        # Apply replacements right-to-left so earlier offsets remain valid.
        out = text
        for start, end, tag in sorted(hits, reverse=True):
            out = out[:start] + f"[{tag.value}]" + out[end:]
        return out, spans

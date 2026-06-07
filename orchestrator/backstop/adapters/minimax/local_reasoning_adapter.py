"""Local (sim) :class:`ReasoningPort` — a real offline grounded-NLG engine.

This is **not** an echo and makes **no** network call. It performs genuine local
reasoning over the evidence and call state supplied on each request:

* ``compose_line`` ranks the request's :class:`EvidenceSnippet` set against the
  redacted call-state text with a real TF-IDF + cosine scorer (the same stdlib
  vector-space model used by the Moss retrieval sim), selects the best-matching
  snippet, chooses a :class:`DialogAct` from a rule table, and realizes one
  audit-safe spoken line by slot-filling a vetted template with the cited
  snippet's key phrase. Different evidence / state therefore yield genuinely
  different, grounded lines. When the best score is below
  :data:`GROUNDING_THRESHOLD` it returns ``grounded=False`` with the shared
  deterministic safe-fallback line and no citations.

* ``interpret_denial`` classifies redacted denial text with a curated CARC/RARC
  lexicon (:mod:`backstop.domain.carc_table`) plus keyword matching: it resolves
  category, CARC/RARC, the recommended :class:`RouteDecision`, the next
  :class:`DialogAct`, and a rebuttal hook grounded in the denial text — marking
  ``ambiguous=True`` when no code or keyword determines the category.

It is deterministic (identical input -> identical output) so it doubles as the
test double for Service-layer tests and as the runtime fallback when MiniMax is
unavailable. It produces the SAME DTOs as the real adapter and reports
``IntegrationMode.SIM`` from :meth:`health`. It never logs message bodies and
never constructs :class:`RedactedText` from raw input — it only re-wraps text
that was already redacted upstream, threading the immutable input forward.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from backstop.adapters.minimax._grounding import (
    GROUNDING_THRESHOLD,
    enforce_word_cap,
    safe_fallback_line,
    subset_citations,
)
from backstop.adapters.text.runbook_corpus import tokenize
from backstop.domain.carc_table import CarcTable
from backstop.domain.enums import DialogAct, IntegrationMode, RouteDecision
from backstop.domain.redacted import SANCTIONED_TOKEN, RedactedText
from backstop.ports.reasoning_port import (
    ComposeLineRequest,
    ComposeLineResult,
    DenialInterpretation,
    EvidenceSnippet,
    InterpretDenialRequest,
    ReasoningHealth,
)

__all__ = ["LocalReasoningAdapter"]

# Maximum words a slot-filled key phrase contributes to a composed line, so the
# realized utterance stays short even before the request's own ``max_words``
# cap is applied.
_KEY_PHRASE_WORDS = 14

# Keyword -> denial category rules used when no CARC/RARC code resolves. Each
# entry maps a compiled lowercase pattern to a (category, route, dialog act)
# triple. Ordered most-specific first; first match wins.
_KEYWORD_RULES: Tuple[Tuple[re.Pattern[str], str, RouteDecision, DialogAct], ...] = (
    (
        re.compile(r"not\s+(?:deemed\s+)?(?:a\s+)?medical(?:ly)?\s+necess"),
        "medical_necessity",
        RouteDecision.APPEAL,
        DialogAct.CITE_POLICY,
    ),
    (
        re.compile(r"medical\s+necess"),
        "medical_necessity",
        RouteDecision.APPEAL,
        DialogAct.CITE_POLICY,
    ),
    (
        re.compile(r"experimental|investigational"),
        "medical_necessity",
        RouteDecision.APPEAL,
        DialogAct.CITE_POLICY,
    ),
    (
        re.compile(r"prior\s+auth|pre[- ]?auth|authorization\s+(?:not|was)"),
        "authorization",
        RouteDecision.APPEAL,
        DialogAct.REBUT,
    ),
    (
        re.compile(r"timely\s+filing|filing\s+limit|filed\s+late"),
        "timely_filing",
        RouteDecision.APPEAL,
        DialogAct.REBUT,
    ),
    (
        re.compile(r"duplicate"),
        "duplicate",
        RouteDecision.APPEAL,
        DialogAct.REBUT,
    ),
    (
        re.compile(r"bundl|inclusive|component\s+of"),
        "bundling",
        RouteDecision.APPEAL,
        DialogAct.CITE_POLICY,
    ),
    (
        re.compile(r"coordination\s+of\s+benefits|other\s+(?:insurance|payer)|cob\b"),
        "coordination_of_benefits",
        RouteDecision.RESUBMIT,
        DialogAct.REQUEST_INFO,
    ),
    (
        re.compile(r"non[- ]?covered|not\s+covered|benefit\s+not"),
        "non_covered",
        RouteDecision.WRITE_OFF,
        DialogAct.REQUEST_INFO,
    ),
    (
        re.compile(r"missing|incomplete|lacks\s+information|submission\s+error"),
        "missing_information",
        RouteDecision.RESUBMIT,
        DialogAct.REQUEST_INFO,
    ),
    (
        re.compile(r"modifier|inconsistent\s+with|procedure\s+code|coding"),
        "coding",
        RouteDecision.RESUBMIT,
        DialogAct.REQUEST_INFO,
    ),
)

# CARC/RARC codes appearing as standalone tokens in the denial text.
_CODE_TOKEN_RE = re.compile(r"\b(?:carc|rarc|co|pr|oa|pi)?[- ]?(\d{1,3})\b", re.IGNORECASE)

# Maps a category to the dialog act / route used when the lexicon supplies a
# category+route but we still need a next act. Falls back to REQUEST_INFO.
_CATEGORY_DIALOG_ACT: Dict[str, DialogAct] = {
    "medical_necessity": DialogAct.CITE_POLICY,
    "authorization": DialogAct.REBUT,
    "timely_filing": DialogAct.REBUT,
    "duplicate": DialogAct.REBUT,
    "bundling": DialogAct.CITE_POLICY,
    "coding": DialogAct.REQUEST_INFO,
    "missing_information": DialogAct.REQUEST_INFO,
    "coordination_of_benefits": DialogAct.REQUEST_INFO,
    "non_covered": DialogAct.REQUEST_INFO,
    "patient_responsibility": DialogAct.CONFIRM,
    "contractual": DialogAct.CONFIRM,
    "coverage": DialogAct.REQUEST_INFO,
}

# Audit-safe line templates keyed by dialog act. ``{phrase}`` is filled with the
# cited evidence key phrase. Every template is generic enough to be PHI-free.
_LINE_TEMPLATES: Dict[DialogAct, str] = {
    DialogAct.CITE_POLICY: "Per the plan policy on file, {phrase}, so this service is supported.",
    DialogAct.REBUT: "That denial conflicts with the record: {phrase}.",
    DialogAct.REQUEST_INFO: "To proceed I need the reference on file for {phrase}.",
    DialogAct.PROVIDE_INFO: "For the record, {phrase}.",
    DialogAct.STATE_PURPOSE: "I'm calling to appeal this denial because {phrase}.",
    DialogAct.CONFIRM: "Confirming the disposition: {phrase}.",
    DialogAct.GREETING: "Good day, I'm calling regarding a denied claim where {phrase}.",
}


@dataclass
class LocalReasoningAdapter:
    """Deterministic, offline grounded-NLG implementation of ``ReasoningPort``.

    Args:
        carc_table: The CARC/RARC lexicon used by ``interpret_denial``.
    """

    carc_table: CarcTable
    _detail: str = field(default="local grounded-NLG", repr=False)

    # ------------------------------------------------------------------ #
    # compose_line
    # ------------------------------------------------------------------ #
    async def compose_line(self, req: ComposeLineRequest) -> ComposeLineResult:
        """Compose one grounded line by ranking and slot-filling evidence.

        Ranks ``req.evidence`` against ``req.call_state`` with TF-IDF cosine,
        picks the top snippet, derives a dialog act, and realizes a templated
        line citing only that snippet. Falls back to the shared safe line when
        no snippet clears :data:`GROUNDING_THRESHOLD`.
        """
        ranked = _rank_evidence(str(req.call_state), req.evidence)
        if not ranked or ranked[0][1] < GROUNDING_THRESHOLD:
            fallback_act = req.dialog_act or DialogAct.REQUEST_INFO
            line = safe_fallback_line(req.max_words)
            return ComposeLineResult(
                line=_rewrap(req.call_state, line),
                dialog_act=fallback_act,
                citations=(),
                grounded=False,
                confidence=0.0,
            )

        best_snippet, best_score = ranked[0]
        act = _choose_dialog_act(str(req.call_state), str(best_snippet.text), req.dialog_act)
        phrase = _key_phrase(str(best_snippet.text))
        template = _LINE_TEMPLATES.get(act, _LINE_TEMPLATES[DialogAct.PROVIDE_INFO])
        raw_line = enforce_word_cap(template.format(phrase=phrase), req.max_words)
        citations = subset_citations(
            [best_snippet.snippet_id],
            [snip.snippet_id for snip in req.evidence],
        )
        return ComposeLineResult(
            line=_rewrap(best_snippet.text, raw_line),
            dialog_act=act,
            citations=citations,
            grounded=bool(citations),
            confidence=_clamp01(best_score),
        )

    # ------------------------------------------------------------------ #
    # interpret_denial
    # ------------------------------------------------------------------ #
    async def interpret_denial(
        self, req: InterpretDenialRequest
    ) -> DenialInterpretation:
        """Classify redacted denial text into category / route / dialog act.

        Resolves a CARC/RARC code first (from the explicit hints or codes found
        in the text), falling back to keyword rules. Marks ``ambiguous=True``
        when neither a code nor a keyword determines the category.
        """
        text = str(req.denial_text)
        carc = req.carc
        rarc = req.rarc

        category: Optional[str] = None
        route: Optional[RouteDecision] = None
        canonical: Optional[str] = None

        # 1) Resolve via an explicit CARC hint or a code embedded in the text.
        code_candidates: List[str] = []
        if carc:
            code_candidates.append(carc)
        code_candidates.extend(_extract_codes(text))
        for code in code_candidates:
            entry = self.carc_table.get(code)
            if entry is not None:
                carc = code
                category = entry.category
                route = entry.default_route
                canonical = entry.canonical_reason
                break

        # 2) Fall back to keyword rules when no code resolved.
        keyword_act: Optional[DialogAct] = None
        if category is None:
            match = _match_keyword(text)
            if match is not None:
                category, route, keyword_act = match

        ambiguous = category is None
        if category is None:
            category = "unclassified"
            route = RouteDecision.APPEAL  # safest default: pursue, don't write off

        next_act = keyword_act or _CATEGORY_DIALOG_ACT.get(category, DialogAct.REQUEST_INFO)
        rebuttal_hook = _rebuttal_hook(text, canonical, category, ambiguous)

        return DenialInterpretation(
            category=category,
            carc=carc,
            rarc=rarc,
            rebuttal_hook=rebuttal_hook,
            recommended_route=route if route is not None else RouteDecision.APPEAL,
            next_dialog_act=next_act,
            ambiguous=ambiguous,
        )

    # ------------------------------------------------------------------ #
    # health
    # ------------------------------------------------------------------ #
    async def health(self) -> ReasoningHealth:
        """Return a sim liveness snapshot (always ready, never raises)."""
        return ReasoningHealth(ok=True, mode=IntegrationMode.SIM, detail=self._detail)


# --------------------------------------------------------------------------- #
# Pure helpers (no I/O, deterministic).
# --------------------------------------------------------------------------- #
def _rank_evidence(
    query: str, evidence: Tuple[EvidenceSnippet, ...]
) -> List[Tuple[EvidenceSnippet, float]]:
    """Rank ``evidence`` against ``query`` by TF-IDF cosine over the snippets.

    Builds a tiny TF-IDF space from the supplied snippets only (so scoring is
    request-local and grounded purely in what the caller provided), then blends
    in any upstream ``score`` so a strong retrieval prior is respected. Returns
    ``(snippet, score)`` pairs sorted descending, ties broken by input order.
    """
    if not evidence:
        return []
    docs = [tokenize(str(snip.text)) for snip in evidence]
    n_docs = len(docs)
    doc_freq: Dict[str, int] = {}
    for tokens in docs:
        for term in set(tokens):
            doc_freq[term] = doc_freq.get(term, 0) + 1
    idf = {
        term: math.log((1.0 + n_docs) / (1.0 + df)) + 1.0
        for term, df in doc_freq.items()
    }
    q_tokens = tokenize(query)
    q_vec = _tfidf_vector(q_tokens, idf)
    q_norm = _l2(q_vec)

    scored: List[Tuple[int, float]] = []
    for i, tokens in enumerate(docs):
        d_vec = _tfidf_vector(tokens, idf)
        d_norm = _l2(d_vec)
        cosine = _cosine(q_vec, q_norm, d_vec, d_norm)
        raw_prior = evidence[i].score
        prior = 0.0 if raw_prior is None else raw_prior
        blended = 0.7 * cosine + 0.3 * _clamp01(prior)
        scored.append((i, blended))

    scored.sort(key=lambda pair: (-pair[1], pair[0]))
    return [(evidence[i], score) for i, score in scored]


def _tfidf_vector(tokens: List[str], idf: Dict[str, float]) -> Dict[str, float]:
    """Return a sublinear-TF * IDF sparse vector for ``tokens``."""
    counts: Dict[str, int] = {}
    for term in tokens:
        if term in idf:
            counts[term] = counts.get(term, 0) + 1
    return {term: (1.0 + math.log(count)) * idf[term] for term, count in counts.items()}


def _l2(vec: Dict[str, float]) -> float:
    """Return the Euclidean norm of a sparse vector."""
    return math.sqrt(sum(w * w for w in vec.values()))


def _cosine(
    a_vec: Dict[str, float], a_norm: float, b_vec: Dict[str, float], b_norm: float
) -> float:
    """Return the cosine similarity of two sparse vectors (0.0 if degenerate)."""
    if a_norm == 0.0 or b_norm == 0.0:
        return 0.0
    if len(a_vec) > len(b_vec):
        a_vec, b_vec = b_vec, a_vec
    dot = sum(weight * b_vec.get(term, 0.0) for term, weight in a_vec.items())
    return dot / (a_norm * b_norm)


def _choose_dialog_act(
    call_state: str, snippet: str, desired: Optional[DialogAct]
) -> DialogAct:
    """Pick a dialog act from a small rule table over the call state + snippet.

    Honors an explicit ``desired`` act when supplied; otherwise applies rules
    (payer asks for a number -> request info; medical-necessity + policy snippet
    -> cite policy; ...) and defaults to providing info.
    """
    if desired is not None:
        return desired
    state_l = call_state.lower()
    snippet_l = snippet.lower()
    if re.search(r"reference\s+number|claim\s+number|member\s+id|give\s+me\s+the", state_l):
        return DialogAct.REQUEST_INFO
    if re.search(r"medical\s+necess|not\s+covered|experimental", state_l) and re.search(
        r"policy|lcd|ncd|coverage|guideline|criteria", snippet_l
    ):
        return DialogAct.CITE_POLICY
    if re.search(r"deny|denied|denial|reject", state_l):
        return DialogAct.REBUT
    if re.search(r"policy|lcd|ncd|guideline|criteria", snippet_l):
        return DialogAct.CITE_POLICY
    return DialogAct.PROVIDE_INFO


def _key_phrase(snippet: str) -> str:
    """Extract a short, audit-safe key phrase from a snippet for slot-filling.

    Takes the most informative leading clause of the first sentence and caps it
    at :data:`_KEY_PHRASE_WORDS` words. Collapses whitespace and trims trailing
    punctuation so the phrase drops cleanly into a template.
    """
    first = re.split(r"(?<=[.!?])\s+", snippet.strip(), maxsplit=1)[0]
    first = re.sub(r"\s+", " ", first).strip().rstrip(".!?,:; ")
    words = first.split()
    if len(words) > _KEY_PHRASE_WORDS:
        first = " ".join(words[:_KEY_PHRASE_WORDS])
    return first or "the documented policy"


def _extract_codes(text: str) -> List[str]:
    """Return candidate CARC/RARC code strings found in ``text`` (digits only)."""
    out: List[str] = []
    for match in _CODE_TOKEN_RE.finditer(text):
        digits = match.group(1).lstrip("0") or match.group(1)
        if digits and digits not in out:
            out.append(digits)
    return out


def _match_keyword(
    text: str,
) -> Optional[Tuple[str, RouteDecision, DialogAct]]:
    """Return the first matching ``(category, route, act)`` keyword rule."""
    lowered = text.lower()
    for pattern, category, route, act in _KEYWORD_RULES:
        if pattern.search(lowered):
            return category, route, act
    return None


def _rebuttal_hook(
    text: str, canonical: Optional[str], category: str, ambiguous: bool
) -> str:
    """Build a short, grounded rebuttal hook from the resolved interpretation."""
    if ambiguous:
        return "Clarify the exact denial reason before selecting a rebuttal angle."
    if canonical:
        return f"Rebut the '{canonical}' basis with the documented record."
    readable = category.replace("_", " ")
    return f"Challenge the {readable} basis with the documented record."


def _rewrap(source: object, text: str) -> RedactedText:
    """Return a :class:`RedactedText` carrying the composed ``text``.

    ``source`` is an inbound :class:`RedactedText` (already redacted upstream);
    it is accepted only to make the provenance explicit at the call site. The
    composed ``text`` is assembled solely from already-redacted snippet/state
    text plus static, PHI-free templates, so re-minting it through the sanctioned
    factory introduces no new PHI. New spans are empty because the original PHI
    offsets do not map onto the freshly composed string.
    """
    del source  # provenance marker only; offsets do not carry over.
    return RedactedText.from_redaction(text, SANCTIONED_TOKEN, spans=())


def _clamp01(value: float) -> float:
    """Clamp ``value`` into the closed unit interval ``[0, 1]``."""
    if value < 0.0:
        return 0.0
    if value > 1.0:
        return 1.0
    return value

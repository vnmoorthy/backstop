"""ReasoningPort — MiniMax grounded reasoning (L2 port).

Two capabilities: ``compose_line`` produces the ONE grounded spoken line whose
citations are a subset of the supplied evidence ids (never fabricated; ungrounded
input yields a safe fallback), and ``interpret_denial`` classifies ambiguous
denial text into category / CARC-RARC / rebuttal hook / route / next dialog act.

MiniMax has no BAA and sits to the right of the redaction boundary, so every
PHI-bearing free-text field on this egress port is typed ``RedactedText`` — the
adapter assumes inbound text is already redacted and never logs bodies.

This module defines the Protocol plus its request/result DTOs only; concrete
adapters live in ``backstop.adapters.minimax``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Protocol, Tuple, runtime_checkable

from backstop.domain.enums import DialogAct, IntegrationMode, RouteDecision
from backstop.domain.redacted import RedactedText

__all__ = [
    "EvidenceSnippet",
    "ComposeLineRequest",
    "ComposeLineResult",
    "InterpretDenialRequest",
    "DenialInterpretation",
    "ReasoningHealth",
    "ReasoningPort",
]


@dataclass(frozen=True)
class EvidenceSnippet:
    """One redacted, citable evidence passage supplied to the reasoner.

    Attributes:
        snippet_id: Stable citation key; only these ids may be cited.
        text: The redacted passage text (egress-safe).
        score: Optional upstream relevance score in ``[0, 1]``.
    """

    snippet_id: str
    text: RedactedText
    score: Optional[float] = None


@dataclass(frozen=True)
class ComposeLineRequest:
    """A request to compose one grounded spoken line from redacted evidence.

    Attributes:
        call_state: Redacted summary of the current call/turn context.
        evidence: The only snippets whose ids may be cited.
        max_words: Hard cap on the composed line length.
        dialog_act: Optional desired dialog act to steer the line.
    """

    call_state: RedactedText
    evidence: Tuple[EvidenceSnippet, ...]
    max_words: int = 40
    dialog_act: Optional[DialogAct] = None


@dataclass(frozen=True)
class ComposeLineResult:
    """The composed grounded line plus its grounding metadata.

    Attributes:
        line: The redacted spoken line to synthesize.
        dialog_act: The dialog act the line realizes.
        citations: Cited snippet ids; always a subset of the supplied ids.
        grounded: Whether the line is grounded in supplied evidence.
        confidence: Composition confidence in ``[0, 1]``.
    """

    line: RedactedText
    dialog_act: DialogAct
    citations: Tuple[str, ...]
    grounded: bool
    confidence: float


@dataclass(frozen=True)
class InterpretDenialRequest:
    """A request to classify ambiguous, redacted denial text.

    Attributes:
        denial_text: Redacted free-text denial reason to interpret.
        carc: Optional CARC code hint already extracted upstream.
        rarc: Optional RARC code hint already extracted upstream.
    """

    denial_text: RedactedText
    carc: Optional[str] = None
    rarc: Optional[str] = None


@dataclass(frozen=True)
class DenialInterpretation:
    """The structured interpretation of a denial.

    Attributes:
        category: Canonical denial category label.
        carc: Resolved CARC code, when determinable.
        rarc: Resolved RARC code, when determinable.
        rebuttal_hook: Short rebuttal angle to pursue on the call.
        recommended_route: The route the interpretation argues for.
        next_dialog_act: The dialog act to take next.
        ambiguous: Whether the denial text was too ambiguous to classify.
    """

    category: str
    carc: Optional[str]
    rarc: Optional[str]
    rebuttal_hook: str
    recommended_route: RouteDecision
    next_dialog_act: DialogAct
    ambiguous: bool


@dataclass(frozen=True)
class ReasoningHealth:
    """Liveness snapshot for the reasoning backend (never raises).

    Attributes:
        ok: Whether the backend is reachable and serving.
        mode: Whether the active adapter is real or sim.
        detail: Optional human-readable status detail.
    """

    ok: bool
    mode: IntegrationMode
    detail: Optional[str] = None


@runtime_checkable
class ReasoningPort(Protocol):
    """Async grounded reasoning over redacted denial context and evidence."""

    async def compose_line(self, req: ComposeLineRequest) -> ComposeLineResult:
        """Compose one grounded spoken line citing only supplied evidence ids."""
        ...

    async def interpret_denial(
        self, req: InterpretDenialRequest
    ) -> DenialInterpretation:
        """Classify redacted denial text into category / route / dialog act."""
        ...

    async def health(self) -> ReasoningHealth:
        """Return a liveness snapshot; never raises."""
        ...

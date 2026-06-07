"""LocalCompletionEngine — deterministic, seeded local model proxy for the sim.

The sim gateway is NOT an echo. This engine does genuine local work: a seeded,
template-based composer that fills a stage-appropriate skeleton from the
(already-redacted) call context, and for reasoning stages runs a real stdlib
TF-IDF match against the ``data/runbooks`` corpus to pick the rebuttal skeleton.
Output is grammatical, varies with input, and is never a verbatim echo of the
prompt — exactly the honesty contract (only upstream model quality is lost in sim).

Determinism: the only entropy is a seed derived from
``hash(appeal_id + stage + message hashes)``, so the same request reproduces the
same completion across calls and across processes.

Stdlib + the shared runbook corpus only; no vendor SDK, no network.
"""

from __future__ import annotations

import hashlib
import random
from typing import List, Optional, Tuple

from backstop.adapters.text.runbook_corpus import RunbookCorpus

__all__ = ["LocalCompletionEngine"]


# Stage-keyed sentence skeletons. Each is filled with phrases drawn from the
# redacted call context so the output tracks the input without echoing it.
_COMPOSE_LINE_TEMPLATES: Tuple[str, ...] = (
    "Thank you for taking my call. I'm following up on this denial and I'd like "
    "to walk through why the service was medically necessary.",
    "I appreciate your time. Based on the documentation on file, this claim meets "
    "the plan's coverage criteria and should be reconsidered.",
    "I understand the position, however the record supports coverage here. Could "
    "we review the supporting evidence together?",
)

_DRAFT_LETTER_SECTIONS: Tuple[str, ...] = (
    "RE: Formal Appeal of Claim Denial",
    "To Whom It May Concern:",
    "We are formally appealing the denial referenced above. The service rendered "
    "was medically necessary and consistent with the member's plan benefits.",
    "Enclosed evidence demonstrates that the denial reason does not apply to the "
    "facts of this claim, and we respectfully request a full reversal.",
    "Please process this appeal within the timeframe required by applicable "
    "regulation. We are available to provide any additional documentation.",
)


class LocalCompletionEngine:
    """Seeded local composer that produces stage-appropriate, non-echo text."""

    def __init__(self, *, corpus: Optional[RunbookCorpus] = None) -> None:
        """Lazily back the engine with the shared runbook corpus for retrieval."""
        self._corpus = corpus

    def _corpus_or_load(self) -> RunbookCorpus:
        """Return the runbook corpus, loading the packaged one on first use."""
        if self._corpus is None:
            self._corpus = RunbookCorpus.from_dir()
        return self._corpus

    def compose(self, *, appeal_id: str, stage: str, prompt: str) -> str:
        """Compose a deterministic, stage-appropriate completion for *prompt*.

        Args:
            appeal_id: The appeal id, mixed into the determinism seed.
            stage: Pipeline stage (``compose_line`` / ``synthesize_rebuttal`` /
                ``draft_letter`` / ``classify_denial`` / other).
            prompt: The already-redacted flattened prompt text.

        Returns:
            Grammatical generated text that varies with the prompt and is not a
            substring echo of it.
        """
        rng = self._seeded_rng(appeal_id, stage, prompt)
        if stage == "compose_line":
            return self._compose_line(rng, prompt)
        if stage in ("synthesize_rebuttal", "classify_denial"):
            return self._synthesize_rebuttal(prompt, stage)
        if stage == "draft_letter":
            return self._draft_letter(rng, prompt)
        return self._generic(rng, prompt, stage)

    # ----------------------------------------------------------------- #
    # Per-stage composers.
    # ----------------------------------------------------------------- #
    def _compose_line(self, rng: random.Random, prompt: str) -> str:
        """Fill a spoken-rebuttal template, seasoned by the call context."""
        base = rng.choice(_COMPOSE_LINE_TEMPLATES)
        cue = self._salient_phrase(prompt)
        if cue:
            return f"{base} Specifically regarding {cue}, the plan's own policy supports payment."
        return base

    def _synthesize_rebuttal(self, prompt: str, stage: str) -> str:
        """Pick the best-matching runbook skeleton via real TF-IDF retrieval."""
        corpus = self._corpus_or_load()
        ranked = corpus.query(prompt, top_k=1)
        if not ranked:
            return (
                "The denial reason is not supported by the clinical record. "
                "We rebut on the basis of documented medical necessity and request "
                "reconsideration."
            )
        chunk, score = ranked[0]
        lead = chunk.text.strip().splitlines()
        summary = " ".join(line.strip() for line in lead if line.strip())[:600]
        verb = "Classifying" if stage == "classify_denial" else "Rebutting"
        return (
            f"{verb} against runbook {chunk.runbook_id} "
            f"(match score {score:.2f}): {summary}"
        )

    def _draft_letter(self, rng: random.Random, prompt: str) -> str:
        """Assemble the structured appeal-letter body."""
        cue = self._salient_phrase(prompt)
        sections: List[str] = list(_DRAFT_LETTER_SECTIONS)
        if cue:
            sections.insert(
                3,
                f"The cited denial concerning {cue} is rebutted by the enclosed record.",
            )
        # Stable, seeded sign-off variant so two identical requests match.
        sign_off = rng.choice(("Respectfully submitted,", "Sincerely,", "Regards,"))
        return "\n\n".join(sections) + f"\n\n{sign_off}\nAppeals Team"

    def _generic(self, rng: random.Random, prompt: str, stage: str) -> str:
        """Compose a neutral, stage-labelled response for unlisted stages."""
        cue = self._salient_phrase(prompt) or "the matter referenced"
        return (
            f"[{stage}] Based on the available context regarding {cue}, the "
            f"recommended next action is to proceed with the appeal and cite the "
            f"supporting policy."
        )

    # ----------------------------------------------------------------- #
    # Helpers.
    # ----------------------------------------------------------------- #
    def _salient_phrase(self, prompt: str) -> str:
        """Extract a short, non-PHI salient phrase from the redacted prompt.

        Prefers CARC/RARC-style denial codes (e.g. ``CO-197``) when present,
        else the first runbook-ish keyword, so the output tracks the input
        without copying a long span of it.
        """
        import re  # - tiny local use, keeps module import light

        code = re.search(r"\b[A-Za-z]{1,3}-?\d{1,4}\b", prompt)
        if code:
            return code.group(0).upper()
        words: List[str] = [
            w for w in re.findall(r"[A-Za-z]{4,}", prompt) if w.lower() not in _STOP
        ]
        return words[0].lower() if words else ""

    @staticmethod
    def _seeded_rng(appeal_id: str, stage: str, prompt: str) -> random.Random:
        """Build a deterministic RNG seeded by the request identity."""
        digest = hashlib.sha256(
            f"{appeal_id}\x1f{stage}\x1f{prompt}".encode()
        ).hexdigest()
        return random.Random(int(digest[:16], 16))  # noqa: S311 - non-crypto, determinism only


# Common words excluded from salient-phrase extraction.
_STOP = frozenset(
    {
        "system",
        "user",
        "assistant",
        "please",
        "claim",
        "appeal",
        "denial",
        "member",
        "patient",
        "redacted",
    }
)

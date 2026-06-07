"""SIM ``RetrievalPort`` adapter — genuine local TF-IDF over the runbooks.

This adapter does *real* lexical retrieval: it delegates to
:class:`backstop.adapters.text.runbook_corpus.RunbookCorpus`, a stdlib TF-IDF +
cosine vector-space index over ``data/runbooks/*.md``. It is never an echo — a
query about CO-197 prior authorization surfaces different chunks than one about
CO-50 medical necessity, and the returned scores are honest cosine similarities
in ``[0, 1]`` sorted descending.

It is the deterministic test double for the contract suite *and* the production
fallback whenever the Moss project keys are absent. It opens no sockets and
imports no vendor SDK, so it runs identically on the host and in CI.
"""

from __future__ import annotations

from typing import Dict, Optional

from backstop.adapters.text.runbook_corpus import RunbookCorpus
from backstop.domain.enums import IntegrationMode
from backstop.domain.errors import RetrievalError
from backstop.ports.retrieval_port import (
    EvidenceChunk,
    RetrievalHealth,
    RetrievalQuery,
    RetrievalResult,
)

__all__ = ["TfidfRetrievalAdapter"]


class TfidfRetrievalAdapter:
    """Rank runbook chunks for a query with a real TF-IDF + cosine engine.

    The fitted corpus is built once at the composition root (via
    :meth:`RunbookCorpus.from_dir`) and injected, so per-call work is just a
    vector projection and cosine ranking — never a re-index.
    """

    def __init__(self, corpus: RunbookCorpus) -> None:
        """Store the pre-fitted runbook corpus to rank against.

        Args:
            corpus: A fitted :class:`RunbookCorpus`. The index must already be
                built; this adapter only queries it.
        """
        self._corpus = corpus

    async def retrieve(self, query: RetrievalQuery) -> RetrievalResult:
        """Return ranked evidence chunks for ``query`` (``len <= top_k``).

        Scores are cosine similarities in ``[0, 1]`` sorted descending; a query
        that overlaps nothing yields an empty result (not an error).

        Args:
            query: The PHI-free retrieval request.

        Returns:
            A :class:`RetrievalResult` whose chunks honour ``query.top_k``.

        Raises:
            RetrievalError: If the underlying corpus was never fitted.
        """
        try:
            ranked = self._corpus.query(query.text, top_k=query.top_k)
        except RuntimeError as exc:  # corpus queried before fit()
            raise RetrievalError("local retrieval corpus is not ready") from exc

        chunks = tuple(
            EvidenceChunk(
                chunk_id=f"{chunk.runbook_id}#{index}",
                text=chunk.text,
                score=_clamp_unit(score),
                source=chunk.source,
                metadata=_chunk_metadata(chunk.runbook_id, chunk.heading),
            )
            for index, (chunk, score) in enumerate(ranked)
        )
        return RetrievalResult(chunks=chunks, query_id=None)

    async def health(self) -> RetrievalHealth:
        """Report liveness; never raises.

        Returns:
            ``ok=True`` iff the injected corpus holds at least one chunk.
        """
        n_chunks = len(self._corpus.chunks)
        ok = n_chunks > 0
        detail = f"tfidf corpus: {n_chunks} chunk(s)"
        return RetrievalHealth(ok=ok, mode=IntegrationMode.SIM, detail=detail)


def _clamp_unit(value: float) -> float:
    """Clamp ``value`` into the closed unit interval ``[0, 1]``."""
    if value < 0.0:
        return 0.0
    if value > 1.0:
        return 1.0
    return value


def _chunk_metadata(runbook_id: str, heading: str) -> Dict[str, str]:
    """Build a non-PHI provenance metadata map for a runbook chunk."""
    metadata: Dict[str, str] = {"doc_type": "runbook", "runbook_id": runbook_id}
    if heading:
        metadata["heading"] = heading
    carc = _extract_carc(runbook_id)
    if carc is not None:
        metadata["carc"] = carc
    return metadata


def _extract_carc(runbook_id: str) -> Optional[str]:
    """Best-effort CARC code extraction from a runbook id (non-PHI surrogate)."""
    lowered = runbook_id.lower()
    for token in lowered.replace("_", "-").split("-"):
        if token.startswith("co") and token[2:].isdigit():
            return token.upper()
        if token.startswith("n") and token[1:].isdigit():
            return token.upper()
    return None

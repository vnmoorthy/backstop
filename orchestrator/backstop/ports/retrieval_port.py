"""RetrievalPort — Moss evidence retrieval (L2 port).

On the denial-reason turn the swarm retrieves the winning rebuttal and precedent
chunks with provenance. The outbound query is built from non-PHI fields only
(CARC/RARC, payer, CPT, place-of-service); the real adapter asserts no PHI leaves
the boundary and raises a domain ``RetrievalError`` (never a vendor exception).
Real (Moss HTTP) and sim (local TF-IDF) index the same source-of-truth runbooks.

This module defines the Protocol plus its request/result DTOs only; concrete
adapters live in ``backstop.adapters.moss``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Optional, Protocol, Tuple, runtime_checkable

from backstop.domain.enums import IntegrationMode

__all__ = [
    "RetrievalQuery",
    "EvidenceChunk",
    "RetrievalResult",
    "RetrievalHealth",
    "RetrievalPort",
]


@dataclass(frozen=True)
class RetrievalQuery:
    """A PHI-free retrieval request for rebuttal evidence.

    The query text is composed strictly from de-identified denial context so it
    can safely reach a BAA-less retrieval backend.

    Attributes:
        text: PHI-free query string (CARC/RARC + payer + CPT + place-of-service).
        top_k: Maximum number of evidence chunks to return.
        timeout_s: Wall-clock budget for the retrieval, in seconds.
        carc: Optional CARC code filter for narrowing the index.
        payer_id: Optional payer identifier filter (non-PHI surrogate).
    """

    text: str
    top_k: int = 5
    timeout_s: float = 2.0
    carc: Optional[str] = None
    payer_id: Optional[str] = None


@dataclass(frozen=True)
class EvidenceChunk:
    """One retrieved passage with provenance and a normalized score.

    Attributes:
        chunk_id: Stable identifier used as a citation key downstream.
        text: The runbook passage text (source-of-truth, never PHI).
        score: Relevance score normalized to ``[0, 1]`` (descending across a result).
        source: Provenance label (e.g. runbook filename or document id).
        metadata: Free-form non-PHI provenance fields (doc_type, payer, carc, ...).
    """

    chunk_id: str
    text: str
    score: float
    source: str
    metadata: Dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class RetrievalResult:
    """An ordered set of evidence chunks for one query.

    Attributes:
        chunks: Evidence chunks ordered by descending score; ``len <= top_k``.
        query_id: Backend-assigned correlation id, when available.
    """

    chunks: Tuple[EvidenceChunk, ...]
    query_id: Optional[str] = None


@dataclass(frozen=True)
class RetrievalHealth:
    """Liveness snapshot for the retrieval backend (never raises).

    Attributes:
        ok: Whether the backend is reachable and serving.
        mode: Whether the active adapter is real or sim.
        detail: Optional human-readable status detail.
    """

    ok: bool
    mode: IntegrationMode
    detail: Optional[str] = None


@runtime_checkable
class RetrievalPort(Protocol):
    """Async evidence retrieval over the rebuttal runbook corpus."""

    async def retrieve(self, query: RetrievalQuery) -> RetrievalResult:
        """Retrieve evidence for ``query`` within ``query.timeout_s``.

        Raises:
            RetrievalError: On backend failure or timeout after bounded retry.
        """
        ...

    async def health(self) -> RetrievalHealth:
        """Return a liveness snapshot; never raises."""
        ...

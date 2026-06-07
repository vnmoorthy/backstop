"""Stdlib TF-IDF + cosine index over the ``data/runbooks/*.md`` corpus.

This adapter is the shared, vendor-free retrieval substrate that backs both the
Moss simulation (``LocalTfidfRetrievalAdapter``) and the MiniMax simulation
(``MiniMaxSimReasoningAdapter``). It performs *real* lexical retrieval — a
classic TF-IDF vector-space model with cosine similarity — using only the
Python standard library. There is no scikit-learn, no numpy, and no network or
vendor SDK on this path, so it adds zero heavy dependencies to CI and runs
identically on the host and in a container.

The index is built once with :meth:`RunbookCorpus.fit` (or eagerly via
:meth:`RunbookCorpus.from_dir`) and queried with :meth:`RunbookCorpus.query`,
which returns ranked ``(chunk, score)`` pairs with scores in ``[0, 1]`` sorted
descending. Chunking splits each runbook into its Markdown sections so a query
about, say, prior authorization surfaces the CO-197 section rather than an
entire document.

Determinism: tokenization, the vocabulary ordering, and the tie-break on equal
scores are all fixed, so repeated queries over the same corpus return identical
rankings.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

__all__ = [
    "Chunk",
    "RunbookCorpus",
    "default_runbooks_dir",
    "tokenize",
]

# Word tokens: lowercase alphanumerics plus a small set of code-like patterns
# (so "co-197" survives as a single meaningful token rather than splitting into
# "co" and "197"). The pattern matches either a CARC/RARC-style code or a plain
# alphanumeric run of length >= 2.
_CODE_RE = re.compile(r"\b[a-z]{1,3}-?\d{1,4}\b")
_WORD_RE = re.compile(r"[a-z0-9]{2,}")

# Markdown front-matter delimiter and ATX heading marker used for chunking.
_FRONT_MATTER_FENCE = "---"
_HEADING_RE = re.compile(r"^#{1,6}\s")


def default_runbooks_dir() -> Path:
    """Return the packaged ``data/runbooks`` directory shipped with backstop.

    Resolved relative to this module's location
    (``backstop/adapters/text/runbook_corpus.py``) so it works regardless of the
    process working directory. Callers may always pass an explicit path to
    :meth:`RunbookCorpus.from_dir` to override this default.
    """
    # parents: text -> adapters -> backstop ; then data/runbooks.
    return Path(__file__).resolve().parents[2] / "data" / "runbooks"


def tokenize(text: str) -> List[str]:
    """Split *text* into lowercase TF-IDF tokens.

    Emits both code-like tokens (e.g. ``co-197``) and ordinary alphanumeric
    word tokens. Tokenization is the single source of truth shared by indexing
    and querying so the two always agree on the vocabulary.
    """
    lowered = text.lower()
    tokens: List[str] = []
    tokens.extend(_CODE_RE.findall(lowered))
    tokens.extend(_WORD_RE.findall(lowered))
    return tokens


@dataclass(frozen=True)
class Chunk:
    """One retrievable unit of a runbook (a Markdown section).

    Attributes:
        runbook_id: Stable id from the source runbook's YAML front matter, or
            the file stem when no ``runbook_id`` key is present.
        heading: The section heading this chunk was cut at (``""`` for the
            leading body before the first heading).
        text: The raw chunk text used for display and citation.
        source: POSIX-style path of the originating ``.md`` file, for
            provenance.
    """

    runbook_id: str
    heading: str
    text: str
    source: str


@dataclass
class RunbookCorpus:
    """A fitted stdlib TF-IDF + cosine index over runbook chunks.

    Construct empty and call :meth:`fit`, or use :meth:`from_dir` to load and
    fit in one step. After fitting, :meth:`query` ranks chunks against a free
    text query by cosine similarity of their smoothed TF-IDF vectors.
    """

    chunks: List[Chunk] = field(default_factory=list)
    _vocab: Dict[str, int] = field(default_factory=dict, repr=False)
    _idf: List[float] = field(default_factory=list, repr=False)
    # Per-chunk sparse TF-IDF vector: term-index -> weight.
    _vectors: List[Dict[int, float]] = field(default_factory=list, repr=False)
    # L2 norm of each chunk vector (precomputed for cosine).
    _norms: List[float] = field(default_factory=list, repr=False)
    _fitted: bool = field(default=False, repr=False)

    # ----------------------------------------------------------------- #
    # Construction.
    # ----------------------------------------------------------------- #
    @classmethod
    def from_dir(cls, path: Optional[Path] = None) -> RunbookCorpus:
        """Load every ``*.md`` runbook under *path* and return a fitted corpus.

        When *path* is ``None`` the packaged :func:`default_runbooks_dir` is
        used. The directory is read in sorted filename order for determinism.
        """
        root = default_runbooks_dir() if path is None else Path(path)
        corpus = cls()
        chunks: List[Chunk] = []
        for md_path in sorted(root.glob("*.md")):
            text = md_path.read_text(encoding="utf-8")
            chunks.extend(_split_runbook(text, md_path))
        corpus.fit(chunks)
        return corpus

    # ----------------------------------------------------------------- #
    # Fitting.
    # ----------------------------------------------------------------- #
    def fit(self, chunks: List[Chunk]) -> RunbookCorpus:
        """Build the TF-IDF index over *chunks* (idempotent, fit-once).

        Computes the vocabulary, smoothed inverse-document-frequency weights,
        and an L2-normalizable TF-IDF vector per chunk. Returns ``self`` so the
        call can be chained.
        """
        self.chunks = list(chunks)
        self._vocab = {}
        self._idf = []
        self._vectors = []
        self._norms = []

        n_docs = len(self.chunks)
        # Per-chunk term-frequency counts and document frequency per term.
        tf_per_chunk: List[Dict[int, int]] = []
        doc_freq: Dict[int, int] = {}

        for chunk in self.chunks:
            counts: Dict[int, int] = {}
            seen: Dict[int, bool] = {}
            for token in tokenize(chunk.text):
                idx = self._vocab.get(token)
                if idx is None:
                    idx = len(self._vocab)
                    self._vocab[token] = idx
                counts[idx] = counts.get(idx, 0) + 1
                if idx not in seen:
                    seen[idx] = True
                    doc_freq[idx] = doc_freq.get(idx, 0) + 1
            tf_per_chunk.append(counts)

        vocab_size = len(self._vocab)
        # Smoothed IDF: ln((1 + N) / (1 + df)) + 1 — strictly positive, so even
        # a term in every chunk keeps a small positive weight.
        self._idf = [0.0] * vocab_size
        for idx in range(vocab_size):
            df = doc_freq.get(idx, 0)
            self._idf[idx] = math.log((1.0 + n_docs) / (1.0 + df)) + 1.0

        # Build per-chunk TF-IDF vectors (sublinear TF: 1 + ln(count)).
        for counts in tf_per_chunk:
            vec: Dict[int, float] = {}
            for idx, count in counts.items():
                tf = 1.0 + math.log(count)
                vec[idx] = tf * self._idf[idx]
            self._vectors.append(vec)
            self._norms.append(_l2_norm(vec))

        self._fitted = True
        return self

    # ----------------------------------------------------------------- #
    # Querying.
    # ----------------------------------------------------------------- #
    def query(self, text: str, top_k: int = 5) -> List[Tuple[Chunk, float]]:
        """Return up to *top_k* ``(chunk, score)`` pairs ranked for *text*.

        Scores are cosine similarities in ``[0, 1]`` sorted descending; ties
        break on the chunk's index for determinism. Chunks with zero overlap
        (score ``0.0``) are omitted, so a query that matches nothing returns an
        empty list rather than raising. Raises :class:`RuntimeError` if the
        corpus was never fitted.
        """
        if not self._fitted:
            raise RuntimeError("RunbookCorpus.query called before fit()")
        if top_k <= 0:
            return []

        q_vec = self._query_vector(text)
        if not q_vec:
            return []
        q_norm = _l2_norm(q_vec)
        if q_norm == 0.0:
            return []

        scored: List[Tuple[int, float]] = []
        for i, (vec, norm) in enumerate(zip(self._vectors, self._norms)):
            if norm == 0.0:
                continue
            sim = _cosine(q_vec, q_norm, vec, norm)
            if sim > 0.0:
                scored.append((i, sim))

        # Descending score, ascending index on ties → fully deterministic.
        scored.sort(key=lambda pair: (-pair[1], pair[0]))
        return [(self.chunks[i], score) for i, score in scored[:top_k]]

    def _query_vector(self, text: str) -> Dict[int, float]:
        """Project *text* into the fitted TF-IDF space (out-of-vocab dropped)."""
        counts: Dict[int, int] = {}
        for token in tokenize(text):
            idx = self._vocab.get(token)
            if idx is None:
                continue  # Term unseen at fit time contributes nothing.
            counts[idx] = counts.get(idx, 0) + 1
        vec: Dict[int, float] = {}
        for idx, count in counts.items():
            tf = 1.0 + math.log(count)
            vec[idx] = tf * self._idf[idx]
        return vec


# --------------------------------------------------------------------------- #
# Module-private helpers (pure functions).
# --------------------------------------------------------------------------- #
def _l2_norm(vec: Dict[int, float]) -> float:
    """Return the Euclidean norm of a sparse vector."""
    return math.sqrt(sum(weight * weight for weight in vec.values()))


def _cosine(
    a_vec: Dict[int, float],
    a_norm: float,
    b_vec: Dict[int, float],
    b_norm: float,
) -> float:
    """Return the cosine similarity of two sparse vectors given their norms.

    Iterates over the smaller vector's keys for efficiency. Assumes both norms
    are strictly positive (callers guard the zero case).
    """
    if len(a_vec) > len(b_vec):
        a_vec, b_vec = b_vec, a_vec
    dot = 0.0
    for idx, weight in a_vec.items():
        other = b_vec.get(idx)
        if other is not None:
            dot += weight * other
    sim = dot / (a_norm * b_norm)
    # Clamp tiny floating-point overshoots so scores stay within [0, 1].
    if sim > 1.0:
        return 1.0
    if sim < 0.0:
        return 0.0
    return sim


def _parse_front_matter_id(lines: List[str]) -> Tuple[str, int]:
    """Return ``(runbook_id, body_start_line)`` from leading YAML front matter.

    Recognizes a ``---`` fenced block at the top of the file and reads a
    ``runbook_id:`` key from it. Returns an empty id and a body start of ``0``
    when no front matter is present.
    """
    if not lines or lines[0].strip() != _FRONT_MATTER_FENCE:
        return "", 0
    runbook_id = ""
    for i in range(1, len(lines)):
        stripped = lines[i].strip()
        if stripped == _FRONT_MATTER_FENCE:
            return runbook_id, i + 1
        if stripped.startswith("runbook_id:"):
            runbook_id = stripped.split(":", 1)[1].strip()
    # Unterminated front matter: treat the whole file as body.
    return runbook_id, 0


def _split_runbook(text: str, md_path: Path) -> List[Chunk]:
    """Split one runbook's Markdown into per-section :class:`Chunk` objects.

    Front matter is stripped (its id is propagated to every chunk) and the body
    is cut at each ATX heading (``#`` .. ``######``). Sections with no
    word-content are dropped. The source path is recorded for provenance.
    """
    lines = text.splitlines()
    runbook_id, body_start = _parse_front_matter_id(lines)
    if not runbook_id:
        runbook_id = md_path.stem
    source = md_path.as_posix()

    chunks: List[Chunk] = []
    current_heading = ""
    current_lines: List[str] = []

    def flush() -> None:
        body = "\n".join(current_lines).strip()
        combined = f"{current_heading}\n{body}".strip() if current_heading else body
        if combined and tokenize(combined):
            chunks.append(
                Chunk(
                    runbook_id=runbook_id,
                    heading=current_heading.lstrip("# ").strip(),
                    text=combined,
                    source=source,
                )
            )

    for line in lines[body_start:]:
        if _HEADING_RE.match(line):
            flush()
            current_heading = line.strip()
            current_lines = []
        else:
            current_lines.append(line)
    flush()
    return chunks

"""Unit tests for the stdlib TF-IDF runbook corpus.

These tests prove the index performs *real* lexical retrieval over the packaged
``data/runbooks/*.md`` corpus — not a string echo — by asserting that two
distinct denial-reason queries surface the correct runbook section first, with a
strictly positive score, and that the returned chunk text differs from the
query. They also lock the public contract: scores are descending in ``[0, 1]``,
``top_k`` bounds the result length, fitting is required before querying, and
rankings are deterministic across runs.
"""

from __future__ import annotations

import re
from typing import List, Tuple

import pytest

from backstop.adapters.text.runbook_corpus import (
    Chunk,
    RunbookCorpus,
    default_runbooks_dir,
    tokenize,
)


@pytest.fixture(scope="module")
def corpus() -> RunbookCorpus:
    """A corpus fitted once over the packaged runbook directory."""
    return RunbookCorpus.from_dir()


def _carc_codes(chunk: Chunk) -> List[str]:
    """Return upper-cased CARC/RARC-style codes mentioned in a chunk."""
    return [m.upper() for m in re.findall(r"\b[A-Za-z]{1,3}-?\d{1,4}\b", chunk.text)]


# --------------------------------------------------------------------------- #
# Headline behaviour: the two load-bearing ranking assertions.
# --------------------------------------------------------------------------- #
def test_prior_auth_query_ranks_co197_first(corpus: RunbookCorpus) -> None:
    """'prior authorization not obtained' ranks the CO-197 chunk first."""
    query = "prior authorization not obtained"
    ranked = corpus.query(query, top_k=5)

    assert ranked, "expected at least one matching chunk"
    top_chunk, top_score = ranked[0]

    # Real ranking, not an echo of the query.
    assert top_score > 0.0
    assert top_chunk.text != query
    assert "CO-197" in _carc_codes(top_chunk)


def test_medical_necessity_query_ranks_co50_first(corpus: RunbookCorpus) -> None:
    """'not medically necessary' ranks the CO-50 chunk first."""
    query = "not medically necessary"
    ranked = corpus.query(query, top_k=5)

    assert ranked, "expected at least one matching chunk"
    top_chunk, top_score = ranked[0]

    assert top_score > 0.0
    assert top_chunk.text != query
    assert "CO-50" in _carc_codes(top_chunk)


# --------------------------------------------------------------------------- #
# Public-contract guards.
# --------------------------------------------------------------------------- #
def test_scores_are_descending_and_in_unit_interval(corpus: RunbookCorpus) -> None:
    """Scores are sorted descending and bounded to ``[0, 1]``."""
    ranked = corpus.query("prior authorization not obtained", top_k=10)
    scores = [score for _, score in ranked]
    assert scores == sorted(scores, reverse=True)
    assert all(0.0 < score <= 1.0 for score in scores)


def test_top_k_bounds_result_length(corpus: RunbookCorpus) -> None:
    """``query`` returns at most ``top_k`` results."""
    ranked = corpus.query("medical necessity peer to peer review", top_k=2)
    assert len(ranked) <= 2


def test_non_positive_top_k_returns_empty(corpus: RunbookCorpus) -> None:
    """A ``top_k`` of zero or below yields no results without raising."""
    assert corpus.query("prior authorization", top_k=0) == []
    assert corpus.query("prior authorization", top_k=-3) == []


def test_no_match_returns_empty_not_raise(corpus: RunbookCorpus) -> None:
    """A query with no corpus overlap returns an empty list, not an error."""
    assert corpus.query("zzzqqxx nonexistent gibberish token", top_k=5) == []


def test_query_before_fit_raises() -> None:
    """Querying an unfitted corpus is a programming error."""
    with pytest.raises(RuntimeError):
        RunbookCorpus().query("anything", top_k=3)


def test_ranking_is_deterministic(corpus: RunbookCorpus) -> None:
    """Repeated identical queries return identical rankings."""
    first: List[Tuple[Chunk, float]] = corpus.query("medical necessity", top_k=5)
    second: List[Tuple[Chunk, float]] = corpus.query("medical necessity", top_k=5)
    assert [(c.text, s) for c, s in first] == [(c.text, s) for c, s in second]


# --------------------------------------------------------------------------- #
# Helpers / loading.
# --------------------------------------------------------------------------- #
def test_default_dir_exists_and_has_runbooks() -> None:
    """The packaged runbook directory exists and is non-empty."""
    root = default_runbooks_dir()
    assert root.is_dir()
    assert list(root.glob("*.md"))


def test_tokenize_preserves_carc_codes() -> None:
    """Tokenization keeps CARC-style codes intact for matching."""
    tokens = tokenize("Denied with CO-197 prior authorization")
    assert "co-197" in tokens
    assert "authorization" in tokens

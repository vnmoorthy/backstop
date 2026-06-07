"""Moss ``RetrievalPort`` adapters (M7).

Two interchangeable implementations of
:class:`backstop.ports.retrieval_port.RetrievalPort`:

* :class:`~backstop.adapters.moss.moss_http_adapter.MossHttpAdapter` — the REAL
  adapter that POSTs the de-identified denial-context query to Moss's
  project-scoped REST API (``POST {MOSS_BASE_URL}/v1/query``) and maps the JSON
  response into the port DTOs.
* :class:`~backstop.adapters.moss.tfidf_retrieval_adapter.TfidfRetrievalAdapter`
  — the SIM adapter that performs *genuine* local TF-IDF + cosine retrieval over
  the ``data/runbooks/*.md`` corpus via
  :class:`backstop.adapters.text.runbook_corpus.RunbookCorpus`.

Both honour the identical port contract and normalize scores to ``[0, 1]`` so the
service layer can swap real for sim with zero changes.
"""

from __future__ import annotations

from backstop.adapters.moss.moss_http_adapter import MossEndpoints, MossHttpAdapter
from backstop.adapters.moss.tfidf_retrieval_adapter import TfidfRetrievalAdapter

__all__ = [
    "MossEndpoints",
    "MossHttpAdapter",
    "TfidfRetrievalAdapter",
]

"""Contract suite for :class:`RetrievalPort` — real (Moss) and sim (TF-IDF).

Both adapters are instantiated and asserted to honour the same port and the same
behavioural contract: ``retrieve`` returns ranked :class:`EvidenceChunk`s with
``len <= top_k``, scores in ``[0, 1]`` sorted descending; a no-match query yields
an empty result (not an error); failures surface as the domain
:class:`RetrievalError`, never a vendor (``httpx``) exception.

The real adapter NEVER hits the network: every request is served by an
``httpx.MockTransport`` whose handler asserts the project-scoped auth headers and
returns canned JSON (or a fault) so the gate runs offline and deterministically.
"""

from __future__ import annotations

from typing import List, Tuple

import httpx
import pytest

from backstop.adapters.moss.moss_http_adapter import MossHttpAdapter
from backstop.adapters.moss.tfidf_retrieval_adapter import TfidfRetrievalAdapter
from backstop.adapters.text.runbook_corpus import RunbookCorpus
from backstop.domain.enums import IntegrationMode
from backstop.domain.errors import BackstopError, RetrievalError
from backstop.ports.retrieval_port import (
    EvidenceChunk,
    RetrievalHealth,
    RetrievalPort,
    RetrievalQuery,
    RetrievalResult,
)

_PROJECT_ID = "proj_test_123"
_PROJECT_KEY = "sk_moss_secret_abc"
_BASE_URL = "https://api.usemoss.dev"

# A realistic de-identified denial-context query (CARC + payer + procedure).
_DENIAL_QUERY = (
    "CO-197 prior authorization not obtained, payer Aetna, CPT 70553 MRI brain, "
    "inpatient, retro-auth allowed?"
)


# --------------------------------------------------------------------------- #
# Fixtures: a fitted local corpus, plus a mock-transport Moss client.
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="module")
def corpus() -> RunbookCorpus:
    """Fit the real runbook corpus once for the module."""
    return RunbookCorpus.from_dir()


@pytest.fixture()
def sim_adapter(corpus: RunbookCorpus) -> TfidfRetrievalAdapter:
    """A genuine local TF-IDF retrieval adapter."""
    return TfidfRetrievalAdapter(corpus)


def _moss_results_handler(request: httpx.Request) -> httpx.Response:
    """Serve a canned ranked /v1/query response, asserting auth + no PHI."""
    assert request.headers["Authorization"] == f"Bearer {_PROJECT_KEY}"
    assert request.headers["X-Moss-Project"] == _PROJECT_ID
    assert request.url.path == "/v1/query"
    # Defense-in-depth: nothing PHI-shaped should be on the wire.
    body_text = request.content.decode("utf-8")
    assert "member" not in body_text.lower()
    return httpx.Response(
        200,
        json={
            "query_id": "q_abc123",
            "results": [
                {
                    "id": "rb-aetna-co197#0",
                    "text": "Cite the precertification on file and request re-adjudication.",
                    "score": 0.91,
                    "metadata": {
                        "source": "aetna_co197.md",
                        "doc_type": "runbook",
                        "carc": "CO-197",
                    },
                },
                {
                    "id": "rb-uhc-co197#1",
                    "text": "UnitedHealthcare retro-auth window allows appeal within 30 days.",
                    "score": 0.42,
                    "metadata": {"source": "uhc_co197.md", "doc_type": "precedent"},
                },
                {
                    # Score deliberately above 1.0 to prove clamping.
                    "id": "rb-overshoot#2",
                    "text": "Edge case chunk.",
                    "score": 1.7,
                    "metadata": {"source": "x.md"},
                },
            ],
        },
    )


def _make_moss_adapter(handler: object) -> MossHttpAdapter:
    """Build a MossHttpAdapter backed by an httpx.MockTransport (no network)."""
    transport = httpx.MockTransport(handler)  # type: ignore[arg-type]
    client = httpx.AsyncClient(transport=transport, base_url=_BASE_URL)
    return MossHttpAdapter(
        client=client,
        project_id=_PROJECT_ID,
        project_key=_PROJECT_KEY,
    )


@pytest.fixture()
def real_adapter() -> MossHttpAdapter:
    """A Moss HTTP adapter wired to a happy-path mock transport."""
    return _make_moss_adapter(_moss_results_handler)


# --------------------------------------------------------------------------- #
# Substitutability: both adapters ARE a RetrievalPort.
# --------------------------------------------------------------------------- #
def test_both_adapters_honour_the_port(
    real_adapter: MossHttpAdapter, sim_adapter: TfidfRetrievalAdapter
) -> None:
    """The contract is over the runtime-checkable port, not a base class."""
    for adapter in (real_adapter, sim_adapter):
        port: RetrievalPort = adapter
        assert isinstance(port, RetrievalPort)


# --------------------------------------------------------------------------- #
# retrieve(): ranked chunks, len <= top_k, scores in [0,1] descending.
# --------------------------------------------------------------------------- #
def _assert_ranked_unit_descending(result: RetrievalResult, top_k: int) -> None:
    """Shared invariant: bounded, normalized, monotonically non-increasing."""
    assert isinstance(result, RetrievalResult)
    chunks: Tuple[EvidenceChunk, ...] = result.chunks
    assert len(chunks) <= top_k
    scores: List[float] = [c.score for c in chunks]
    for score in scores:
        assert 0.0 <= score <= 1.0
    assert scores == sorted(scores, reverse=True)
    for chunk in chunks:
        assert isinstance(chunk, EvidenceChunk)
        assert chunk.chunk_id
        assert chunk.text


async def test_sim_retrieve_is_ranked_bounded_and_normalized(
    sim_adapter: TfidfRetrievalAdapter,
) -> None:
    """The SIM adapter does genuine ranked retrieval over the runbooks."""
    result = await sim_adapter.retrieve(RetrievalQuery(text=_DENIAL_QUERY, top_k=3))
    _assert_ranked_unit_descending(result, top_k=3)
    assert len(result.chunks) > 0  # The corpus contains CO-197 runbooks.


async def test_real_retrieve_is_ranked_bounded_and_normalized(
    real_adapter: MossHttpAdapter,
) -> None:
    """The REAL adapter maps Moss JSON into ranked, normalized chunks."""
    result = await real_adapter.retrieve(RetrievalQuery(text=_DENIAL_QUERY, top_k=5))
    _assert_ranked_unit_descending(result, top_k=5)
    assert result.query_id == "q_abc123"
    # The 1.7 overshoot must have been clamped to exactly 1.0.
    assert max(c.score for c in result.chunks) == 1.0
    # Provenance handle carried through from metadata.source for every chunk.
    by_id = {c.chunk_id: c for c in result.chunks}
    assert by_id["rb-aetna-co197#0"].source == "aetna_co197.md"
    assert by_id["rb-aetna-co197#0"].metadata["carc"] == "CO-197"


async def test_real_respects_top_k_bound(real_adapter: MossHttpAdapter) -> None:
    """top_k caps the result even when the backend returns more rows."""
    result = await real_adapter.retrieve(RetrievalQuery(text=_DENIAL_QUERY, top_k=2))
    assert len(result.chunks) == 2


async def test_sim_returns_query_relevant_chunks(
    sim_adapter: TfidfRetrievalAdapter,
) -> None:
    """Different denial reasons surface different top chunks (real retrieval)."""
    co197 = await sim_adapter.retrieve(
        RetrievalQuery(text="CO-197 prior authorization precertification", top_k=1)
    )
    co50 = await sim_adapter.retrieve(
        RetrievalQuery(text="CO-50 medical necessity not covered", top_k=1)
    )
    assert co197.chunks and co50.chunks
    assert co197.chunks[0].chunk_id != co50.chunks[0].chunk_id


# --------------------------------------------------------------------------- #
# No-match -> empty result (never an error).
# --------------------------------------------------------------------------- #
async def test_sim_no_match_returns_empty(sim_adapter: TfidfRetrievalAdapter) -> None:
    """A query that overlaps nothing yields an empty result, not an error."""
    result = await sim_adapter.retrieve(
        RetrievalQuery(text="zzzzz qqqqq nonexistentvocabularyterm", top_k=5)
    )
    assert result.chunks == ()


async def test_real_no_match_returns_empty() -> None:
    """An empty Moss results array maps to an empty RetrievalResult."""

    def empty_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"query_id": "q_empty", "results": []})

    adapter = _make_moss_adapter(empty_handler)
    result = await adapter.retrieve(RetrievalQuery(text=_DENIAL_QUERY, top_k=5))
    assert result.chunks == ()
    assert result.query_id == "q_empty"


# --------------------------------------------------------------------------- #
# Failure modes: domain RetrievalError, never httpx.
# --------------------------------------------------------------------------- #
async def test_real_raises_domain_error_on_persistent_5xx() -> None:
    """5xx after the one bounded retry surfaces as RetrievalError, not httpx."""
    calls = {"n": 0}

    def flaky_handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(503, json={"error": "unavailable"})

    adapter = _make_moss_adapter(flaky_handler)
    with pytest.raises(RetrievalError) as excinfo:
        await adapter.retrieve(RetrievalQuery(text=_DENIAL_QUERY, top_k=5))
    assert not isinstance(excinfo.value, httpx.HTTPError)
    assert isinstance(excinfo.value, BackstopError)
    # Exactly one bounded retry: initial attempt + one more = 2 calls.
    assert calls["n"] == 2


async def test_real_retries_once_then_succeeds() -> None:
    """A single 5xx is recovered by the one bounded retry."""
    calls = {"n": 0}

    def recovering_handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(500, json={"error": "transient"})
        return httpx.Response(200, json={"query_id": "q_ok", "results": []})

    adapter = _make_moss_adapter(recovering_handler)
    result = await adapter.retrieve(RetrievalQuery(text=_DENIAL_QUERY, top_k=5))
    assert result.query_id == "q_ok"
    assert calls["n"] == 2


async def test_real_raises_domain_error_on_timeout() -> None:
    """A transport timeout surfaces as RetrievalError after the bounded retry."""

    def timeout_handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow backend", request=request)

    adapter = _make_moss_adapter(timeout_handler)
    with pytest.raises(RetrievalError):
        await adapter.retrieve(RetrievalQuery(text=_DENIAL_QUERY, top_k=5))


async def test_real_raises_domain_error_on_4xx() -> None:
    """An auth/client error is terminal and surfaces as RetrievalError."""

    def unauthorized_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "bad key"})

    adapter = _make_moss_adapter(unauthorized_handler)
    with pytest.raises(RetrievalError):
        await adapter.retrieve(RetrievalQuery(text=_DENIAL_QUERY, top_k=5))


async def test_real_rejects_phi_before_egress() -> None:
    """The PHI guard trips BEFORE any network call (defense-in-depth)."""
    calls = {"n": 0}

    def counting_handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, json={"results": []})

    adapter = _make_moss_adapter(counting_handler)
    phi_query = RetrievalQuery(
        text="appeal for member id 123456789 SSN 078-05-1120", top_k=5
    )
    with pytest.raises(RetrievalError):
        await adapter.retrieve(phi_query)
    assert calls["n"] == 0  # Nothing left the boundary.


# --------------------------------------------------------------------------- #
# health(): never raises; honest mode badge.
# --------------------------------------------------------------------------- #
async def test_sim_health_reports_sim_mode(
    sim_adapter: TfidfRetrievalAdapter,
) -> None:
    """The sim adapter reports ok + SIM over a non-empty corpus."""
    health = await sim_adapter.health()
    assert isinstance(health, RetrievalHealth)
    assert health.ok is True
    assert health.mode is IntegrationMode.SIM


async def test_sim_health_false_on_empty_corpus() -> None:
    """An unfitted/empty corpus reports not-ok but never raises."""
    adapter = TfidfRetrievalAdapter(RunbookCorpus())
    health = await adapter.health()
    assert health.ok is False
    assert health.mode is IntegrationMode.SIM


async def test_real_health_reports_real_mode() -> None:
    """The real adapter reports REAL mode and never raises on probe."""

    def health_handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/health"
        return httpx.Response(200, json={"status": "ok"})

    adapter = _make_moss_adapter(health_handler)
    health = await adapter.health()
    assert health.ok is True
    assert health.mode is IntegrationMode.REAL


async def test_real_health_never_raises_on_fault() -> None:
    """A transport fault during health is folded into ok=False (no raise)."""

    def boom_handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route", request=request)

    adapter = _make_moss_adapter(boom_handler)
    health = await adapter.health()
    assert health.ok is False
    assert health.mode is IntegrationMode.REAL

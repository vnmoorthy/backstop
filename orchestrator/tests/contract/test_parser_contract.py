"""Contract suite for :class:`DenialParserPort` — real (mocked) + sim.

The load-bearing assertion of this milestone: BOTH adapters — the real
``UnsiloedDenialParserAdapter`` driven against a mocked ``httpx`` transport and
the deterministic ``DeterministicDenialParserAdapter`` — are substitutable
through the one ``DenialParserPort`` and return the identical
``DenialExtraction`` shape. Each ``ExtractedField`` carries a confidence in
``[0, 1]`` plus source provenance; ``overall_confidence`` is in ``[0, 1]``; and
``needs_human_review`` is a real ``bool`` driven by the confidence floor.

The network is never touched: the real adapter is exercised exclusively through
``httpx.MockTransport`` (a missing SDK would still leave the sim as the reference
double, but ``httpx`` is a declared dev dependency here). The EDI deep-parse test
proves the sim does genuine X12 work — CLP/CAS/NM1/DTM/MIA segments map to the
right claim/amounts/CARC/RARC/NPI/DOS — not an echo.
"""

from __future__ import annotations

import json
from typing import Any, Awaitable, Callable, Dict, List

import httpx
import pytest

from backstop.adapters.unsiloed import _denial_schema as fields
from backstop.adapters.unsiloed.deterministic_parser_adapter import (
    DeterministicDenialParserAdapter,
)
from backstop.adapters.unsiloed.errors import (
    UnsiloedAuthError,
    UnsiloedJobFailed,
    UnsiloedTimeout,
    UnsupportedArtifact,
)
from backstop.adapters.unsiloed.unsiloed_http_adapter import (
    UnsiloedDenialParserAdapter,
)
from backstop.domain.enums import ArtifactKind
from backstop.ports.denial_parser_port import (
    DenialExtraction,
    DenialParserPort,
    ExtractedField,
    ParseRequest,
)

# --------------------------------------------------------------------------- #
# Synthetic fixtures (all bytes — never a server-side path).
# --------------------------------------------------------------------------- #
# A real synthetic 835 ERA with a CO-197 prior-auth denial.
SYNTHETIC_835 = (
    b"ISA*00*          *00*          *ZZ*PAYER          *ZZ*PROVIDER       "
    b"*260314*1200*^*00501*000000001*0*P*:~"
    b"GS*HP*PAYER*PROVIDER*20260314*1200*1*X*005010X221A1~"
    b"ST*835*0001~"
    b"BPR*I*0.00*C*ACH~"
    b"N1*PR*AETNA HEALTH~"
    b"CLP*CLM-55-7741*4*2480.00*0.00*0.00*MC*9988776655~"
    b"CAS*CO*197*2480.00~"
    b"NM1*QC*1*DOE*JANE****MI*W812340099~"
    b"NM1*82*1*SMITH*JOHN****XX*1659302341~"
    b"NM1*85*2*GENERAL HOSPITAL*****XX*1093847551~"
    b"DTM*472*20260314~"
    b"MIA*0***N130~"
    b"SVC*HC:99285*2480.00*0.00~"
    b"SE*15*0001~"
)

# Synthetic EOB text with a CO-45 fee-schedule denial.
SYNTHETIC_EOB = (
    b"CIGNA EXPLANATION OF BENEFITS\n"
    b"Member ID: W554433221\n"
    b"Claim Number: CLM-99-2210\n"
    b"Date of Service: 2026-02-11\n"
    b"Billed: $1,850.00  Allowed: $0.00  Paid: $0.00\n"
    b"Reason Code: CO-45  Remark: N130\n"
    b"Rendering NPI 1659302341  Billing NPI 1093847551\n"
)

# A minimal PDF-ish blob; the real adapter only ships these bytes, never opens
# a server path, so the contents are irrelevant to the (mocked) transport.
SYNTHETIC_PDF = b"%PDF-1.4 synthetic denial scan"


# --------------------------------------------------------------------------- #
# A mocked Unsiloed transport: POST /v2/extract -> job, GET /extract/{id} -> map.
# --------------------------------------------------------------------------- #
def _success_body() -> Dict[str, Any]:
    """Return a terminal flat field-map mirroring the Unsiloed contract."""
    return {
        "status": "Succeeded",
        "min_confidence_score": 0.91,
        "payer_name": {"value": "Aetna", "score": 0.93, "page_no": 1, "bboxes": []},
        "member_id": {"value": "W812340099", "score": 0.9, "page_no": 1, "bboxes": []},
        "claim_number": {
            "value": "CLM-55-7741",
            "score": 0.97,
            "page_no": 1,
            "bboxes": [
                {
                    "bbox": [10, 20, 30, 40],
                    "text": "CLM-55-7741",
                    "confidence": 0.97,
                    "page_width": 612,
                    "page_height": 792,
                }
            ],
        },
        "billing_npi": {"value": "1093847551", "score": 0.92, "page_no": 1, "bboxes": []},
        "date_of_service": {"value": "2026-03-14", "score": 0.9, "page_no": 1, "bboxes": []},
        "billed_amount": {"value": 2480.0, "score": 0.9, "page_no": 1, "bboxes": []},
        "carc_codes": {"value": ["197"], "score": 0.95, "page_no": 1, "bboxes": []},
        "denial_reason": {
            "value": "Precertification/authorization absent",
            "score": 0.88,
            "page_no": 1,
            "bboxes": [],
        },
    }


def _make_transport(
    captured: Dict[str, Any],
    *,
    processing_polls: int = 1,
) -> httpx.MockTransport:
    """Build a MockTransport that emulates the async create+poll job model."""
    state = {"polls": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and request.url.path == "/v2/extract":
            captured["api_key"] = request.headers.get("api-key")
            captured["authorization"] = request.headers.get("authorization")
            body = request.content
            captured["has_schema_data"] = b'name="schema_data"' in body
            captured["has_file"] = b'name="file"' in body
            captured["body"] = body
            return httpx.Response(
                201,
                json={"job_id": "job-xyz", "status": "Processing", "quota_remaining": 7},
            )
        if request.method == "GET" and request.url.path == "/extract/job-xyz":
            captured.setdefault("poll_api_key", request.headers.get("api-key"))
            state["polls"] += 1
            if state["polls"] <= processing_polls:
                return httpx.Response(200, json={"status": "Processing"})
            captured["total_polls"] = state["polls"]
            return httpx.Response(200, json=_success_body())
        return httpx.Response(404, json={"error": "not found"})

    return httpx.MockTransport(handler)


# --------------------------------------------------------------------------- #
# Adapter builders used by the parametrized contract tests.
# --------------------------------------------------------------------------- #
RealRequest = ParseRequest(content=SYNTHETIC_PDF, kind=ArtifactKind.EOB, filename="eob.pdf")
SimRequest = ParseRequest(content=SYNTHETIC_835, kind=ArtifactKind.X12_835)


async def _build_real() -> DenialExtraction:
    """Drive the real adapter to a terminal extraction over a mocked transport."""
    captured: Dict[str, Any] = {}
    transport = _make_transport(captured)
    async with httpx.AsyncClient(transport=transport) as client:
        adapter = UnsiloedDenialParserAdapter(
            client=client, api_key="unit-secret-key", poll_max_attempts=6
        )
        assert isinstance(adapter, DenialParserPort)
        return await adapter.parse(RealRequest)


async def _build_sim() -> DenialExtraction:
    """Drive the sim adapter to a terminal extraction over a synthetic 835."""
    adapter = DeterministicDenialParserAdapter()
    assert isinstance(adapter, DenialParserPort)
    return await adapter.parse(SimRequest)


_BUILDERS: Dict[str, Callable[[], Awaitable[DenialExtraction]]] = {
    "real": _build_real,
    "sim": _build_sim,
}


# --------------------------------------------------------------------------- #
# Shared contract tests (both adapters).
# --------------------------------------------------------------------------- #
@pytest.mark.contract
@pytest.mark.parametrize("name", sorted(_BUILDERS))
async def test_returns_denial_extraction(name: str) -> None:
    """Both adapters return a well-formed ``DenialExtraction``."""
    result = await _BUILDERS[name]()
    assert isinstance(result, DenialExtraction)
    assert isinstance(result.kind, ArtifactKind)
    assert isinstance(result.fields, tuple)
    assert result.fields, "extraction must populate at least one field"


@pytest.mark.contract
@pytest.mark.parametrize("name", sorted(_BUILDERS))
async def test_each_field_confidence_and_provenance(name: str) -> None:
    """Every ``ExtractedField`` has a confidence in [0,1] and provenance."""
    result = await _BUILDERS[name]()
    for field in result.fields:
        assert isinstance(field, ExtractedField)
        assert isinstance(field.name, str) and field.name
        assert isinstance(field.value, str)
        assert isinstance(field.confidence, float)
        assert 0.0 <= field.confidence <= 1.0
        assert isinstance(field.provenance, dict)
        assert field.provenance, f"field {field.name!r} carries no provenance"
        if field.page_no is not None:
            assert isinstance(field.page_no, int)


@pytest.mark.contract
@pytest.mark.parametrize("name", sorted(_BUILDERS))
async def test_overall_confidence_and_review_flag(name: str) -> None:
    """``overall_confidence`` is in [0,1] and ``needs_human_review`` is bool."""
    result = await _BUILDERS[name]()
    assert isinstance(result.overall_confidence, float)
    assert 0.0 <= result.overall_confidence <= 1.0
    assert isinstance(result.needs_human_review, bool)


@pytest.mark.contract
@pytest.mark.parametrize("name", sorted(_BUILDERS))
async def test_required_denial_fields_present(name: str) -> None:
    """Both adapters surface the core denial fields the pipeline needs."""
    result = await _BUILDERS[name]()
    names = {f.name for f in result.fields}
    for required in (
        fields.PAYER_NAME,
        fields.CLAIM_NUMBER,
        fields.DATE_OF_SERVICE,
        fields.BILLED_AMOUNT,
        fields.CARC_CODES,
        fields.DENIAL_REASON,
    ):
        assert required in names, f"{name} adapter missing {required}"


@pytest.mark.contract
async def test_supports_capability_probe() -> None:
    """The real adapter refuses EDI; the sim is the universal fallback."""
    sim = DeterministicDenialParserAdapter()
    for kind in ArtifactKind:
        assert sim.supports(kind) is True

    transport = httpx.MockTransport(lambda request: httpx.Response(204))
    async with httpx.AsyncClient(transport=transport) as client:
        real = UnsiloedDenialParserAdapter(client=client, api_key="k")
        assert real.supports(ArtifactKind.EOB) is True
        assert real.supports(ArtifactKind.CMS1500) is True
        assert real.supports(ArtifactKind.PDF_IMAGE) is True
        assert real.supports(ArtifactKind.X12_835) is False
        assert real.supports(ArtifactKind.X12_837) is False
        assert real.supports(ArtifactKind.X12_277) is False


# --------------------------------------------------------------------------- #
# Sim deep-parse: genuine X12 work, not an echo.
# --------------------------------------------------------------------------- #
def _field_map(result: DenialExtraction) -> Dict[str, ExtractedField]:
    """Index an extraction's fields by canonical name."""
    return {f.name: f for f in result.fields}


@pytest.mark.contract
async def test_sim_edi_835_real_parse() -> None:
    """A synthetic 835 with CLP/CAS/NM1/DTM/MIA parses to the right values."""
    adapter = DeterministicDenialParserAdapter()
    result = await adapter.parse(SimRequest)
    by = _field_map(result)

    # CLP01 claim number, CLP03/04 billed/paid amounts.
    assert by[fields.CLAIM_NUMBER].value == "CLM-55-7741"
    assert by[fields.BILLED_AMOUNT].value == "2480.00"
    assert by[fields.PAID_AMOUNT].value == "0.00"

    # CAS carries the CARC denial code; MIA the RARC remark.
    assert by[fields.CARC_CODES].value == "197"
    assert "N130" in by[fields.RARC_CODES].value

    # NM1*82 rendering NPI (NM109) and NM1*85 billing NPI.
    assert by[fields.RENDERING_NPI].value == "1659302341"
    assert by[fields.BILLING_NPI].value == "1093847551"

    # DTM*472 date of service.
    assert by[fields.DATE_OF_SERVICE].value == "20260314"

    # N1*PR payer name.
    assert by[fields.PAYER_NAME].value == "AETNA HEALTH"

    # Deterministic parse ⇒ certainty, and provenance is the literal segment.
    clp = by[fields.CLAIM_NUMBER]
    assert clp.confidence == 1.0
    assert clp.provenance["source_text"].startswith("CLP*CLM-55-7741")
    assert clp.provenance["engine"] == "x12"

    # CARC code 197 expands to its canonical prior-auth reason via the table.
    assert "precertification" in by[fields.DENIAL_REASON].value.lower()
    assert result.overall_confidence == 1.0
    assert result.needs_human_review is False


@pytest.mark.contract
async def test_sim_eob_text_regex_trips_review() -> None:
    """EOB text yields a CARC, an expanded reason, and a tripped review gate."""
    adapter = DeterministicDenialParserAdapter()
    result = await adapter.parse(
        ParseRequest(content=SYNTHETIC_EOB, kind=ArtifactKind.EOB)
    )
    by = _field_map(result)
    assert by[fields.CARC_CODES].value == "45"
    assert by[fields.MEMBER_ID].value == "W554433221"
    assert by[fields.CLAIM_NUMBER].value == "CLM-99-2210"
    assert by[fields.DENIAL_REASON].value  # expanded via CARC table
    # Heuristic confidence stays in the [0.6, 0.9] band per field.
    for name in (fields.CARC_CODES, fields.CLAIM_NUMBER, fields.MEMBER_ID):
        assert 0.6 <= by[name].confidence <= 0.9
    # Mean below the 0.85 floor ⇒ human review.
    assert result.needs_human_review is True


@pytest.mark.contract
async def test_sim_unmapped_carc_degrades_gracefully() -> None:
    """An unknown CARC degrades to an 'Unmapped code' reason, not a crash."""
    edi = (
        b"ST*835*0001~"
        b"CLP*CLM-1*4*100.00*0.00*0.00~"
        b"CAS*CO*999*100.00~"
        b"SE*4*0001~"
    )
    adapter = DeterministicDenialParserAdapter()
    result = await adapter.parse(ParseRequest(content=edi, kind=ArtifactKind.X12_835))
    by = _field_map(result)
    assert by[fields.CARC_CODES].value == "999"
    assert by[fields.DENIAL_REASON].value == "Unmapped code 999"
    assert by[fields.DENIAL_REASON].confidence <= 0.5


# --------------------------------------------------------------------------- #
# Real adapter: async job model, header, schema, mapping, error normalization.
# --------------------------------------------------------------------------- #
@pytest.mark.contract
async def test_real_extract_happy_path_mocked() -> None:
    """Real adapter sends api-key + schema_data, polls, maps the field-map."""
    captured: Dict[str, Any] = {}
    transport = _make_transport(captured, processing_polls=2)
    async with httpx.AsyncClient(transport=transport) as client:
        adapter = UnsiloedDenialParserAdapter(
            client=client, api_key="unit-secret-key", poll_max_attempts=6
        )
        result = await adapter.parse(RealRequest)

    # Credential travels in the custom api-key header, NOT Bearer.
    assert captured["api_key"] == "unit-secret-key"
    assert captured["authorization"] is None
    # schema_data + file shipped as multipart; bytes only, never a path.
    assert captured["has_schema_data"] is True
    assert captured["has_file"] is True
    # Async job model: it polled past the Processing responses to terminal.
    assert captured["total_polls"] == 3

    by = _field_map(result)
    # score -> confidence; min_confidence_score -> overall_confidence.
    assert by[fields.CLAIM_NUMBER].value == "CLM-55-7741"
    assert by[fields.CLAIM_NUMBER].confidence == pytest.approx(0.97)
    assert by[fields.CLAIM_NUMBER].page_no == 1
    # bbox provenance carried through.
    assert by[fields.CLAIM_NUMBER].provenance["bbox"] == "10,20,30,40"
    assert by[fields.CLAIM_NUMBER].provenance["source_text"] == "CLM-55-7741"
    # list value rendered as joined string.
    assert by[fields.CARC_CODES].value == "197"
    assert result.overall_confidence == pytest.approx(0.91)
    assert result.needs_human_review is False


@pytest.mark.contract
async def test_real_schema_data_is_denial_json_schema() -> None:
    """The ``schema_data`` form field is the stringified Denial JSON Schema."""
    captured: Dict[str, Any] = {}
    transport = _make_transport(captured)
    async with httpx.AsyncClient(transport=transport) as client:
        adapter = UnsiloedDenialParserAdapter(client=client, api_key="k")
        await adapter.parse(RealRequest)

    body = captured["body"].decode("utf-8", errors="replace")
    # The multipart body embeds the JSON schema with our canonical fields.
    expected = json.dumps(fields.build_denial_schema())
    assert expected in body
    assert fields.CARC_CODES in body
    assert fields.CLAIM_NUMBER in body


@pytest.mark.contract
async def test_real_refuses_edi() -> None:
    """Raw EDI raises ``UnsupportedArtifact`` and never hits the network."""
    polled: List[str] = []

    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        polled.append(str(request.url))
        return httpx.Response(200, json={})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        adapter = UnsiloedDenialParserAdapter(client=client, api_key="k")
        for kind in (ArtifactKind.X12_835, ArtifactKind.X12_837, ArtifactKind.X12_277):
            with pytest.raises(UnsupportedArtifact):
                await adapter.parse(ParseRequest(content=SYNTHETIC_835, kind=kind))
    assert polled == [], "EDI must never reach the network"


@pytest.mark.contract
async def test_real_auth_error_is_normalized() -> None:
    """A 401 from create is normalized to ``UnsiloedAuthError`` (no key leak)."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "unauthorized"})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        adapter = UnsiloedDenialParserAdapter(client=client, api_key="bad-key")
        with pytest.raises(UnsiloedAuthError) as exc:
            await adapter.parse(RealRequest)
    # The secret never appears in the error message.
    assert "bad-key" not in str(exc.value)
    assert exc.value.status_code == 401


@pytest.mark.contract
async def test_real_job_failure_is_normalized() -> None:
    """A terminal ``Failed`` status is normalized to ``UnsiloedJobFailed``."""
    state = {"polls": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(201, json={"job_id": "job-xyz", "status": "Processing"})
        state["polls"] += 1
        return httpx.Response(200, json={"status": "Failed", "message": "bad doc"})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        adapter = UnsiloedDenialParserAdapter(client=client, api_key="k")
        with pytest.raises(UnsiloedJobFailed):
            await adapter.parse(RealRequest)
    assert state["polls"] == 1


@pytest.mark.contract
async def test_real_poll_timeout_is_normalized() -> None:
    """Exhausting the poll budget raises ``UnsiloedTimeout`` (bounded retries)."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(201, json={"job_id": "job-xyz", "status": "Processing"})
        return httpx.Response(200, json={"status": "Processing"})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        adapter = UnsiloedDenialParserAdapter(
            client=client, api_key="k", poll_max_attempts=3
        )
        with pytest.raises(UnsiloedTimeout):
            await adapter.parse(RealRequest)


@pytest.mark.contract
async def test_confidence_floor_drives_review_flag() -> None:
    """``needs_human_review`` follows the injected confidence floor identically."""
    captured: Dict[str, Any] = {}
    transport = _make_transport(captured)
    # min_confidence_score is 0.91; a 0.95 floor must trip review.
    async with httpx.AsyncClient(transport=transport) as client:
        strict = UnsiloedDenialParserAdapter(
            client=client, api_key="k", confidence_floor=0.95
        )
        result = await strict.parse(RealRequest)
    assert result.overall_confidence == pytest.approx(0.91)
    assert result.needs_human_review is True

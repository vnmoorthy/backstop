"""Tests for :class:`IngestDenialService`.

Pins: ingestion runs under the admission gate (slot acquired/released), the
extraction is audit-wrapped (hashes only, never raw bytes), and raw EDI falls
back from the real parser to the deterministic sim parser.
"""

from __future__ import annotations

import asyncio
import hashlib

from backstop.domain.enums import ArtifactKind
from backstop.ports.denial_parser_port import ParseRequest
from backstop.services.ingest_denial_service import IngestDenialService
from tests.services.fakes import FakeAudit, FakeParser, SemaphoreGate

# The real parser handles image artifacts but declines raw EDI.
_REAL_KINDS = (ArtifactKind.EOB, ArtifactKind.CMS1500, ArtifactKind.UB04)


def _service(
    gate: SemaphoreGate, audit: FakeAudit
) -> tuple[IngestDenialService, FakeParser, FakeParser]:
    """Wire an ingest service with a real (image-only) and sim (all) parser."""
    real = FakeParser(supported=_REAL_KINDS)
    sim = FakeParser(supported=None)
    service = IngestDenialService(
        gate=gate, primary_parser=real, fallback_parser=sim, audit=audit
    )
    return service, real, sim


async def test_image_artifact_uses_real_parser_and_audits() -> None:
    """An EOB image is parsed by the real parser and audit-appended."""
    audit = FakeAudit()
    service, real, sim = _service(SemaphoreGate(2), audit)
    req = ParseRequest(content=b"\x89PNG-eob", kind=ArtifactKind.EOB, filename="eob.png")

    result = await service.ingest("appeal-1", req)

    assert result.used_fallback is False
    assert len(real.parse_calls) == 1
    assert len(sim.parse_calls) == 0
    # Audit record holds only a content hash, never the raw bytes.
    assert len(audit.records) == 1
    record = audit.records[0]
    assert record.appeal_id == "appeal-1"
    assert record.prompt_sha256 == hashlib.sha256(req.content).hexdigest()
    assert result.audit_id == "audit-1"


async def test_raw_edi_falls_back_to_sim_parser() -> None:
    """Raw 835 EDI is declined by the real parser and routed to the sim."""
    audit = FakeAudit()
    service, real, sim = _service(SemaphoreGate(2), audit)
    req = ParseRequest(content=b"ISA*00*...835", kind=ArtifactKind.X12_835)

    result = await service.ingest("appeal-9", req)

    assert result.used_fallback is True
    assert len(real.parse_calls) == 0
    assert len(sim.parse_calls) == 1
    # Fallback is recorded as SIM mode in the audit record.
    assert audit.records[0].mode.value == "sim"


async def test_ingest_runs_under_the_gate() -> None:
    """The parse holds an admission slot; the gate is empty afterwards."""
    gate = SemaphoreGate(1)
    audit = FakeAudit()
    service, _real, _sim = _service(gate, audit)

    req = ParseRequest(content=b"img", kind=ArtifactKind.EOB)
    await service.ingest("appeal-1", req)

    snapshot = await gate.capacity()
    assert snapshot.in_use == 0


async def test_concurrent_ingests_capped_by_gate() -> None:
    """With cap=2, the gate's high-water mark never exceeds the cap."""
    gate = SemaphoreGate(2)
    audit = FakeAudit()
    service, _real, _sim = _service(gate, audit)

    reqs = [
        ParseRequest(content=f"img-{i}".encode(), kind=ArtifactKind.EOB)
        for i in range(6)
    ]
    await asyncio.gather(
        *(service.ingest(f"appeal-{i}", r) for i, r in enumerate(reqs))
    )

    assert gate.max_in_use <= 2
    assert (await gate.capacity()).in_use == 0
    assert len(audit.records) == 6

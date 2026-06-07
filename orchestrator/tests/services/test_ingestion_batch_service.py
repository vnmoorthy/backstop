"""Tests for :class:`IngestionBatchService`.

Pins: a multi-claim EDI batch is split into one parse per claim, each is
ingested (and thereby audit-wrapped), and the per-artifact gate caps the
concurrent fan-out.
"""

from __future__ import annotations

from typing import List, Sequence, Tuple

from backstop.domain.enums import ArtifactKind
from backstop.ports.denial_parser_port import ParseRequest
from backstop.services.ingest_denial_service import IngestDenialService
from backstop.services.ingestion_batch_service import IngestionBatchService
from tests.services.fakes import FakeAudit, FakeParser, SemaphoreGate


def _splitter(content: bytes) -> Sequence[Tuple[str, ParseRequest]]:
    """Split an 835 batch into one part per ``CLP`` claim-loop segment."""
    raw = content.decode()
    # A claim is any tilde-delimited segment that opens a CLP loop; the leading
    # ISA envelope segment is not a claim and is dropped.
    claims = [seg for seg in raw.split("~") if seg.startswith("CLP")]
    parts: List[Tuple[str, ParseRequest]] = []
    for i, claim in enumerate(claims):
        parts.append(
            (
                f"appeal-{i}",
                ParseRequest(
                    content=claim.encode(),
                    kind=ArtifactKind.X12_835,
                ),
            )
        )
    return parts


def _service(gate: SemaphoreGate, audit: FakeAudit) -> IngestionBatchService:
    """Wire a batch service over a real+sim ingest service and the splitter."""
    ingest = IngestDenialService(
        gate=gate,
        primary_parser=FakeParser(supported=(ArtifactKind.EOB,)),
        fallback_parser=FakeParser(supported=None),
        audit=audit,
    )
    return IngestionBatchService(gate=gate, ingest=ingest, splitter=_splitter)


async def test_splits_multi_claim_edi_into_per_claim_ingests() -> None:
    """Three claims in one batch produce three ingested items (sim fallback)."""
    gate = SemaphoreGate(4)
    audit = FakeAudit()
    service = _service(gate, audit)
    batch = b"ISA~CLP1*22*100~CLP2*22*200~CLP3*22*300"

    result = await service.ingest_batch(batch)

    assert result.claim_count == 3
    assert len(result.items) == 3
    assert [item.appeal_id for item in result.items] == [
        "appeal-0",
        "appeal-1",
        "appeal-2",
    ]
    # Each split claim was EDI → sim fallback, and each was audited.
    assert all(item.result.used_fallback for item in result.items)
    assert len(audit.records) == 3


async def test_fan_out_throttled_by_capacity() -> None:
    """The gate cap bounds concurrent per-claim ingests."""
    gate = SemaphoreGate(2)
    audit = FakeAudit()
    service = _service(gate, audit)
    batch = b"ISA" + b"".join(f"~CLP{i}*22*{i}00".encode() for i in range(8))

    result = await service.ingest_batch(batch)

    assert result.claim_count == 8
    assert gate.max_in_use <= 2
    assert (await gate.capacity()).in_use == 0


async def test_empty_batch_yields_no_items() -> None:
    """A batch with no claims ingests nothing and reports the capacity."""
    gate = SemaphoreGate(3)
    audit = FakeAudit()
    service = _service(gate, audit)

    result = await service.ingest_batch(b"ISA~")

    assert result.claim_count == 0
    assert result.items == ()
    assert result.capacity == 3
    assert audit.records == []

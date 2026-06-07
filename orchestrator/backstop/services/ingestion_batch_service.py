"""IngestionBatchService — split multi-claim EDI and throttle by capacity.

A single uploaded EDI file (an 835 remittance, say) carries many claims. This
service splits the batch into one :class:`ParseRequest` per claim, ensures the
admission gate has enough capacity for the fan-out, then ingests every part
through the per-artifact :class:`IngestDenialService` — which itself caps
concurrency. Splitting is delegated to an injected, pure splitter so this
service holds only the batching/throttling policy.

The split bytes never leave memory and no server-side path is derived from
caller input. The service depends only on ports and the per-artifact service.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Callable, List, Sequence, Tuple

from backstop.ports.concurrency_gate_port import ConcurrencyGatePort
from backstop.ports.denial_parser_port import ParseRequest
from backstop.services.ingest_denial_service import (
    IngestDenialService,
    IngestResult,
)

__all__ = ["BatchItem", "BatchResult", "IngestionBatchService"]

#: Split one batch artifact into ``(per-claim appeal_id, ParseRequest)`` parts.
BatchSplitter = Callable[[bytes], Sequence[Tuple[str, ParseRequest]]]


@dataclass(frozen=True)
class BatchItem:
    """One ingested claim from a batch.

    Attributes:
        appeal_id: The appeal the split claim was filed under.
        result: The per-artifact ingest result.
    """

    appeal_id: str
    result: IngestResult


@dataclass(frozen=True)
class BatchResult:
    """The outcome of ingesting a whole batch.

    Attributes:
        items: One :class:`BatchItem` per successfully-ingested claim.
        claim_count: The number of claims the splitter found.
        capacity: The gate capacity available for the fan-out.
    """

    items: Tuple[BatchItem, ...]
    claim_count: int
    capacity: int


class IngestionBatchService:
    """Split a multi-claim EDI batch and ingest its claims under throttle."""

    def __init__(
        self,
        *,
        gate: ConcurrencyGatePort,
        ingest: IngestDenialService,
        splitter: BatchSplitter,
    ) -> None:
        """Store the gate, the per-artifact ingest service, and the splitter."""
        self._gate = gate
        self._ingest = ingest
        self._splitter = splitter

    async def ingest_batch(self, content: bytes) -> BatchResult:
        """Split ``content`` into claims and ingest each under capacity throttle.

        Ensures the gate has at least ``claim_count`` (clamped) capacity before
        the fan-out, then ingests every claim concurrently. Each per-artifact
        ingest acquires its own slot, so the gate cap is the true throttle.
        """
        claims = list(self._splitter(content))
        claim_count = len(claims)
        if claim_count == 0:
            cap = (await self._gate.capacity()).capacity
            return BatchResult(items=(), claim_count=0, capacity=cap)

        capacity = await self._gate.ensure_capacity(target=claim_count)

        tasks: List[asyncio.Task[BatchItem]] = [
            asyncio.ensure_future(self._ingest_one(appeal_id, req))
            for appeal_id, req in claims
        ]
        items = tuple(await asyncio.gather(*tasks))
        return BatchResult(
            items=items,
            claim_count=claim_count,
            capacity=capacity,
        )

    async def _ingest_one(
        self, appeal_id: str, req: ParseRequest
    ) -> BatchItem:
        """Ingest one split claim and wrap its result."""
        result = await self._ingest.ingest(appeal_id, req)
        return BatchItem(appeal_id=appeal_id, result=result)

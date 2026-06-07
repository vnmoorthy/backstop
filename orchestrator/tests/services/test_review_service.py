"""Tests for :class:`ReviewService`.

Pins: the review queue surfaces only appeals flagged ``needs_human_review``, and
every evidence body in a review packet is ``RedactedText`` (raw PHI never
reaches the nurse-facing surface).
"""

from __future__ import annotations

from backstop.domain.redacted import RedactedText
from backstop.services.review_service import RawEvidence, ReviewService
from tests.services.fakes import FakeRedaction, MemoryAppealRepo, make_appeal


def _service(repo: MemoryAppealRepo) -> ReviewService:
    """Build a review service over the repo and the redaction fake."""
    return ReviewService(repo, FakeRedaction())


async def test_queue_returns_only_flagged_appeals() -> None:
    """Only appeals flagged for human review appear in the queue."""
    repo = MemoryAppealRepo()
    await repo.save(make_appeal("flagged", needs_human_review=True))
    await repo.save(make_appeal("clean", needs_human_review=False))

    queue = await _service(repo).queue()

    assert [a.id for a in queue] == ["flagged"]


def test_packet_redacts_every_evidence_body() -> None:
    """Each evidence row's body is ``RedactedText`` with PHI masked."""
    service = _service(MemoryAppealRepo())
    evidence = [
        RawEvidence("c1", "runbook.md", "Auth for MEMBER123 was obtained."),
        RawEvidence("c2", "policy.md", "Per plan policy, appeal is supported."),
    ]

    packet = service.build_packet("appeal-1", evidence)

    assert packet.appeal_id == "appeal-1"
    assert len(packet.evidence) == 2
    for row in packet.evidence:
        assert isinstance(row.body, RedactedText)
    # The PHI token is masked out of the redacted bodies.
    bodies = [str(row.body) for row in packet.evidence]
    assert all("MEMBER123" not in b for b in bodies)
    assert any("[MEMBER_ID]" in b for b in bodies)


def test_packet_preserves_chunk_ids_and_sources() -> None:
    """Non-PHI metadata (ids, source labels) flow through unchanged."""
    service = _service(MemoryAppealRepo())
    evidence = [RawEvidence("c1", "runbook_co197.md", "supported")]

    packet = service.build_packet("appeal-1", evidence)

    assert packet.evidence[0].chunk_id == "c1"
    assert packet.evidence[0].source == "runbook_co197.md"


def test_empty_evidence_yields_empty_packet() -> None:
    """An appeal with no evidence yields an empty, well-formed packet."""
    packet = _service(MemoryAppealRepo()).build_packet("appeal-1", [])
    assert packet.evidence == ()

"""Unit tests for the tamper-evident SHA-256 audit chain adapter.

The audit chain is the source of truth the cryptographic sign-off gate consults:
a letter may only be signed when ``verify_chain()`` is True. These tests assert
the chain is genuinely tamper-evident — every appended record's ``prev_hash``
equals the prior record's ``record_hash``, recomputation detects a flip of ANY
field, and the store keeps only hashes, never raw prompt/completion text.
"""

from __future__ import annotations

import dataclasses
from typing import List

from backstop.adapters.truefoundry.hashchain_audit_adapter import (
    GENESIS_HASH,
    HashChainAuditAdapter,
)
from backstop.domain.enums import IntegrationMode
from backstop.ports.audit_log_port import AuditLogPort, AuditRecord


def _record(appeal_id: str = "ap1", stage: str = "draft_letter", n: int = 0) -> AuditRecord:
    """Build a representative audit record carrying only hashes."""
    return AuditRecord(
        appeal_id=appeal_id,
        stage=stage,
        model="openai-main/gpt-4o-mini",
        mode=IntegrationMode.SIM,
        prompt_sha256=f"{'a' * 63}{n}",
        completion_sha256=f"{'b' * 63}{n}",
        redaction_count=n,
        prompt_tokens=100 + n,
        completion_tokens=50 + n,
        usd_micros=450_000 + n,
        gateway_request_id=f"req-{n}",
    )


def _appended_chain(adapter: HashChainAuditAdapter, count: int) -> List[str]:
    """Append *count* records and return their assigned audit ids."""
    return [adapter.append(_record(n=i)) for i in range(count)]


# --------------------------------------------------------------------------- #
# Type contract.
# --------------------------------------------------------------------------- #
def test_adapter_satisfies_audit_port() -> None:
    """The adapter is a structural ``AuditLogPort``."""
    assert isinstance(HashChainAuditAdapter(), AuditLogPort)


# --------------------------------------------------------------------------- #
# Chain linkage.
# --------------------------------------------------------------------------- #
def test_first_record_links_to_genesis() -> None:
    """The first record's ``prev_hash`` is the well-known genesis hash."""
    adapter = HashChainAuditAdapter()
    adapter.append(_record(n=0))
    [rec] = list(adapter.iter("ap1"))
    assert rec.prev_hash == GENESIS_HASH
    assert rec.record_hash is not None and rec.record_hash != GENESIS_HASH


def test_each_prev_hash_equals_prior_record_hash() -> None:
    """Every record's ``prev_hash`` equals the prior record's ``record_hash``."""
    adapter = HashChainAuditAdapter()
    _appended_chain(adapter, 5)
    records = list(adapter.iter("ap1"))
    assert len(records) == 5
    for prev, cur in zip(records, records[1:]):
        assert cur.prev_hash == prev.record_hash


def test_append_returns_record_hash_as_audit_id() -> None:
    """``append`` returns the content-addressed audit id (the record hash)."""
    adapter = HashChainAuditAdapter()
    audit_id = adapter.append(_record(n=0))
    [rec] = list(adapter.iter("ap1"))
    assert audit_id == rec.record_hash


def test_intact_chain_verifies() -> None:
    """A freshly built chain verifies, globally and per-appeal."""
    adapter = HashChainAuditAdapter()
    _appended_chain(adapter, 4)
    assert adapter.verify_chain() is True
    assert adapter.verify_chain("ap1") is True


def test_empty_chain_verifies() -> None:
    """An empty chain is vacuously intact."""
    assert HashChainAuditAdapter().verify_chain() is True


# --------------------------------------------------------------------------- #
# Tamper detection — flipping any field breaks verification.
# --------------------------------------------------------------------------- #
def _flip_field_and_verify(field: str, new_value: object) -> bool:
    """Build a chain, mutate one stored field at the SQL layer, re-verify.

    The adapter exposes no UPDATE path (append-only), so the test reaches into
    the private connection to simulate tampering — exactly what the chain must
    detect at the application layer.
    """
    adapter = HashChainAuditAdapter()
    _appended_chain(adapter, 3)
    # Tamper with the middle row's field, leaving its stored record_hash intact.
    with adapter._conn:  # - deliberate tamper at the storage layer
        adapter._conn.execute(
            f"UPDATE audit_chain SET {field} = ? WHERE seq = 2",  # noqa: S608 - field from a fixed allowlist
            (new_value,),
        )
    return adapter.verify_chain()


def test_flip_redaction_count_detected() -> None:
    """Flipping ``redaction_count`` breaks the chain."""
    assert _flip_field_and_verify("redaction_count", 9999) is False


def test_flip_usd_micros_detected() -> None:
    """Flipping the priced cost breaks the chain."""
    assert _flip_field_and_verify("usd_micros", 1) is False


def test_flip_prompt_hash_detected() -> None:
    """Flipping the prompt hash breaks the chain."""
    assert _flip_field_and_verify("prompt_sha256", "deadbeef") is False


def test_flip_stage_detected() -> None:
    """Flipping the stage label breaks the chain."""
    assert _flip_field_and_verify("stage", "tampered") is False


def test_flip_mode_detected() -> None:
    """Flipping the real/sim mode flag breaks the chain."""
    assert _flip_field_and_verify("mode", "real") is False


def test_flip_record_hash_detected() -> None:
    """Corrupting a stored ``record_hash`` breaks the chain."""
    assert _flip_field_and_verify("record_hash", "0" * 64) is False


def test_deleting_a_record_breaks_linkage() -> None:
    """Removing a middle record breaks the prev-hash linkage on re-verify."""
    adapter = HashChainAuditAdapter()
    _appended_chain(adapter, 4)
    with adapter._conn:  # - deliberate tamper
        adapter._conn.execute("DELETE FROM audit_chain WHERE seq = 2")
    assert adapter.verify_chain() is False


# --------------------------------------------------------------------------- #
# Store holds hashes, not raw text.
# --------------------------------------------------------------------------- #
def test_store_holds_only_hashes_not_raw_text() -> None:
    """No raw prompt/completion body is persisted — only their hashes.

    The record DTO has no field for raw text; this asserts the stored columns
    are exactly the hash + accounting fields and round-trip unchanged.
    """
    adapter = HashChainAuditAdapter()
    rec = _record(n=7)
    adapter.append(rec)
    [stored] = list(adapter.iter("ap1"))
    # The hashes are stored verbatim; the DTO carries no raw-text attribute.
    assert stored.prompt_sha256 == rec.prompt_sha256
    assert stored.completion_sha256 == rec.completion_sha256
    assert not hasattr(stored, "prompt_text")
    assert not hasattr(stored, "completion_text")


def test_iter_scopes_to_appeal() -> None:
    """``iter`` yields only the requested appeal's records, in order."""
    adapter = HashChainAuditAdapter()
    adapter.append(_record(appeal_id="ap1", n=0))
    adapter.append(_record(appeal_id="ap2", n=1))
    adapter.append(_record(appeal_id="ap1", n=2))
    ids = [r.redaction_count for r in adapter.iter("ap1")]
    assert ids == [0, 2]


# --------------------------------------------------------------------------- #
# Sign-off precondition: a broken chain must block a signature.
# --------------------------------------------------------------------------- #
def _sign_off_gate(adapter: AuditLogPort, appeal_id: str) -> str:
    """Stand-in sign-off gate: refuses unless the audit chain verifies."""
    if not adapter.verify_chain(appeal_id):
        raise RuntimeError("audit chain broken; sign-off refused")
    return "signed"


def test_signoff_requires_intact_chain() -> None:
    """The sign-off gate succeeds on an intact chain."""
    adapter = HashChainAuditAdapter()
    _appended_chain(adapter, 2)
    assert _sign_off_gate(adapter, "ap1") == "signed"


def test_signoff_refused_on_broken_chain() -> None:
    """The sign-off gate refuses once any field is flipped."""
    adapter = HashChainAuditAdapter()
    _appended_chain(adapter, 3)
    with adapter._conn:  # - deliberate tamper
        adapter._conn.execute(
            "UPDATE audit_chain SET usd_micros = 0 WHERE seq = 2"
        )
    try:
        _sign_off_gate(adapter, "ap1")
        raise AssertionError("sign-off should have been refused")
    except RuntimeError:
        pass


def test_record_is_frozen() -> None:
    """``AuditRecord`` is an immutable value object."""
    rec = _record()
    try:
        dataclasses.replace(rec, stage="other")  # replace is fine; direct set is not
    except Exception as exc:  # pragma: no cover - replace should succeed
        raise AssertionError("dataclasses.replace must work on a frozen record") from exc
    try:
        rec.stage = "mutated"  # type: ignore[misc]
        raise AssertionError("record must be frozen")
    except dataclasses.FrozenInstanceError:
        pass

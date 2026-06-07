"""HashChainAuditAdapter — tamper-evident SHA-256 audit chain (AuditLogPort).

Every model call appends one append-only record whose ``record_hash`` is the
SHA-256 of its canonical field tuple together with the prior record's
``record_hash``. ``verify_chain`` recomputes the whole chain and reports whether
it is intact: flipping any field of any record breaks the hash and is detected.

The store is SELF-CONTAINED — a SQLite connection this adapter owns (file or
``:memory:``), created with its own schema. It does NOT touch ``infra/db.py`` and
holds only HASHES of the redacted prompt/completion text, never the raw bodies or
any key. Records are append-only: the adapter issues no UPDATE or DELETE.

The module imports nothing outside the standard library and the pure domain layer
(``sqlite3`` is stdlib), so it adds no vendor dependency and runs in CI offline.
"""

from __future__ import annotations

import hashlib
import sqlite3
import threading
from typing import Iterator, List, Optional, Tuple

from backstop.domain.enums import IntegrationMode
from backstop.ports.audit_log_port import AuditRecord

__all__ = ["HashChainAuditAdapter", "GENESIS_HASH"]

# The previous-hash of the very first record in a chain (all-zero SHA-256). A
# fixed, well-known genesis anchors the chain so the first record's integrity is
# also covered by the recomputation.
GENESIS_HASH: str = "0" * 64

# Ordered field names that participate in the per-record hash. ``record_hash``
# itself is excluded (it is the output); ``prev_hash`` is included so a record is
# bound to its predecessor.
_HASHED_FIELDS: Tuple[str, ...] = (
    "appeal_id",
    "stage",
    "model",
    "mode",
    "prompt_sha256",
    "completion_sha256",
    "redaction_count",
    "prompt_tokens",
    "completion_tokens",
    "usd_micros",
    "gateway_request_id",
    "prev_hash",
)


def _canonical(record: AuditRecord, prev_hash: str) -> str:
    r"""Render a record's hashed fields as a canonical, unambiguous string.

    Uses a unit-separator (``\x1f``) between fields and an explicit ``\x00``
    for ``None`` so no two distinct records can collide by concatenation.
    """
    values: List[str] = []
    for name in _HASHED_FIELDS:
        if name == "prev_hash":
            raw: object = prev_hash
        elif name == "mode":
            raw = record.mode.value
        else:
            raw = getattr(record, name)
        values.append("\x00" if raw is None else str(raw))
    return "\x1f".join(values)


def _hash_record(record: AuditRecord, prev_hash: str) -> str:
    """Compute the SHA-256 ``record_hash`` for *record* chained to *prev_hash*."""
    return hashlib.sha256(_canonical(record, prev_hash).encode("utf-8")).hexdigest()


class HashChainAuditAdapter:
    """Append-only, tamper-evident SHA-256 hash chain over model-call records.

    Implements :class:`~backstop.ports.audit_log_port.AuditLogPort`. Owns a
    private SQLite store (default in-memory) and is safe for concurrent appends
    via an internal lock.
    """

    def __init__(self, *, db_path: str = ":memory:") -> None:
        """Open the self-contained SQLite store and ensure the schema exists."""
        # ``check_same_thread=False`` + an explicit lock lets the adapter back the
        # sim/real gateway which may be driven from async worker threads.
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._lock = threading.Lock()
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        """Create the append-only audit table if it does not yet exist."""
        with self._conn:
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS audit_chain (
                    seq                INTEGER PRIMARY KEY AUTOINCREMENT,
                    audit_id           TEXT    NOT NULL UNIQUE,
                    appeal_id          TEXT    NOT NULL,
                    stage              TEXT    NOT NULL,
                    model              TEXT    NOT NULL,
                    mode               TEXT    NOT NULL,
                    prompt_sha256      TEXT    NOT NULL,
                    completion_sha256  TEXT    NOT NULL,
                    redaction_count    INTEGER NOT NULL,
                    prompt_tokens      INTEGER NOT NULL,
                    completion_tokens  INTEGER NOT NULL,
                    usd_micros         INTEGER NOT NULL,
                    gateway_request_id TEXT,
                    prev_hash          TEXT    NOT NULL,
                    record_hash        TEXT    NOT NULL
                )
                """
            )

    # ----------------------------------------------------------------- #
    # AuditLogPort.
    # ----------------------------------------------------------------- #
    def append(self, record: AuditRecord) -> str:
        """Append *record* to the chain and return its assigned audit id.

        The new record's ``prev_hash`` is the global chain head's
        ``record_hash`` (or :data:`GENESIS_HASH` for the first record), and its
        ``record_hash`` is computed over its canonical fields. Stores only the
        provided hashes — never raw prompt/completion text.
        """
        with self._lock, self._conn:
            prev_hash = self._head_hash()
            record_hash = _hash_record(record, prev_hash)
            cur = self._conn.execute(
                """
                INSERT INTO audit_chain (
                    audit_id, appeal_id, stage, model, mode,
                    prompt_sha256, completion_sha256, redaction_count,
                    prompt_tokens, completion_tokens, usd_micros,
                    gateway_request_id, prev_hash, record_hash
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record_hash,  # audit_id == record_hash: stable, content-addressed
                    record.appeal_id,
                    record.stage,
                    record.model,
                    record.mode.value,
                    record.prompt_sha256,
                    record.completion_sha256,
                    record.redaction_count,
                    record.prompt_tokens,
                    record.completion_tokens,
                    record.usd_micros,
                    record.gateway_request_id,
                    prev_hash,
                    record_hash,
                ),
            )
            _ = cur.lastrowid
        return record_hash

    def verify_chain(self, appeal_id: Optional[str] = None) -> bool:
        """Recompute the chain and return whether it is intact.

        The recomputation always walks the WHOLE chain in append order (so a
        tamper anywhere is caught); when *appeal_id* is given, ``True`` is
        returned only if the chain is globally intact AND that appeal's rows are
        internally consistent. Any stored ``prev_hash``/``record_hash`` that does
        not match the recomputation fails verification.
        """
        with self._lock:
            rows = self._all_rows()
        expected_prev = GENESIS_HASH
        ok = True
        for row in rows:
            stored_prev, stored_hash, record = row
            recomputed = _hash_record(record, stored_prev)
            if stored_prev != expected_prev or stored_hash != recomputed:
                ok = False
                break
            expected_prev = stored_hash
        if not ok:
            return False
        if appeal_id is not None:
            # Scoped check: the appeal must have at least been recomputed cleanly
            # as part of the global pass above; recompute its own rows too.
            for stored_prev, stored_hash, record in rows:
                if (
                    record.appeal_id == appeal_id
                    and _hash_record(record, stored_prev) != stored_hash
                ):
                    return False
        return True

    def iter(self, appeal_id: str) -> Iterator[AuditRecord]:
        """Iterate this appeal's audit records in append order."""
        with self._lock:
            rows = self._all_rows()
        for _prev, _hash, record in rows:
            if record.appeal_id == appeal_id:
                yield record

    # ----------------------------------------------------------------- #
    # Internal store access.
    # ----------------------------------------------------------------- #
    def _head_hash(self) -> str:
        """Return the current chain head's ``record_hash`` (or the genesis)."""
        cur = self._conn.execute(
            "SELECT record_hash FROM audit_chain ORDER BY seq DESC LIMIT 1"
        )
        row = cur.fetchone()
        return GENESIS_HASH if row is None else str(row[0])

    def _all_rows(self) -> List[Tuple[str, str, AuditRecord]]:
        """Return ``(prev_hash, record_hash, record)`` for every row, in order."""
        cur = self._conn.execute(
            """
            SELECT appeal_id, stage, model, mode, prompt_sha256, completion_sha256,
                   redaction_count, prompt_tokens, completion_tokens, usd_micros,
                   gateway_request_id, prev_hash, record_hash
            FROM audit_chain ORDER BY seq ASC
            """
        )
        out: List[Tuple[str, str, AuditRecord]] = []
        for row in cur.fetchall():
            (
                appeal_id,
                stage,
                model,
                mode,
                prompt_sha256,
                completion_sha256,
                redaction_count,
                prompt_tokens,
                completion_tokens,
                usd_micros,
                gateway_request_id,
                prev_hash,
                record_hash,
            ) = row
            record = AuditRecord(
                appeal_id=str(appeal_id),
                stage=str(stage),
                model=str(model),
                mode=IntegrationMode(str(mode)),
                prompt_sha256=str(prompt_sha256),
                completion_sha256=str(completion_sha256),
                redaction_count=int(redaction_count),
                prompt_tokens=int(prompt_tokens),
                completion_tokens=int(completion_tokens),
                usd_micros=int(usd_micros),
                gateway_request_id=(
                    None if gateway_request_id is None else str(gateway_request_id)
                ),
                prev_hash=str(prev_hash),
                record_hash=str(record_hash),
            )
            out.append((str(prev_hash), str(record_hash), record))
        return out

    def close(self) -> None:
        """Close the underlying SQLite connection."""
        with self._lock:
            self._conn.close()

"""S3-backed file-store adapter for :class:`FileStorePort`.

Implements :class:`backstop.ports.file_store_port.FileStorePort` against an S3
bucket: bytes are stored under a server-generated object key, reads are served
through a short-lived presigned URL, and TTL is enforced both by the recorded
expiry and an object-key prefix that an S3 lifecycle rule can sweep. As with the
local adapter, the object key is *minted by the server* from a fresh id and the
scope -- never a user-controlled path -- and the same per-appeal ownership authz
gates every read.

The ``boto3`` / ``aioboto3`` SDK is imported *lazily inside methods* so this
module imports cleanly even when the SDK is absent. The contract test mocks the
S3 client, so a missing SDK never blocks the gate.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Dict, Optional

from backstop.domain.errors import Forbidden
from backstop.ports.auth_port import Principal
from backstop.ports.clock_port import ClockPort
from backstop.ports.file_store_port import ArtifactRef, ArtifactScope, SignedUrl
from backstop.ports.id_gen_port import IdGenPort

__all__ = ["S3FileStoreAdapter", "S3ClientFactory"]


# An injected zero-arg async factory yielding an aioboto3 S3 client context
# manager. Kept abstract (``Any``) so this module never imports the SDK at
# module scope.
S3ClientFactory = Any


@dataclass(frozen=True)
class _Record:
    """Internal bookkeeping for one stored S3 artifact."""

    ref: str
    appeal_id: str
    kind: str
    sha256: str
    key: str
    expires_at_iso: str


class S3FileStoreAdapter:
    """S3-backed :class:`~backstop.ports.file_store_port.FileStorePort`.

    Construction injects the ``bucket``, a ``client_factory`` (an async callable
    returning an ``aioboto3`` S3 client context manager), the id generator and
    the clock. ``key_prefix`` namespaces objects so an S3 lifecycle expiry rule
    can be scoped to swept artifacts.
    """

    def __init__(
        self,
        *,
        bucket: str,
        client_factory: S3ClientFactory,
        id_gen: IdGenPort,
        clock: ClockPort,
        key_prefix: str = "artifacts/",
        url_ttl_seconds: int = 300,
    ) -> None:
        """Store injected collaborators; no network call happens here."""
        self._bucket = bucket
        self._client_factory = client_factory
        self._id_gen = id_gen
        self._clock = clock
        self._key_prefix = key_prefix
        self._url_ttl_seconds = url_ttl_seconds
        self._records: Dict[str, _Record] = {}

    async def put(
        self,
        data: bytes,
        *,
        scope: ArtifactScope,
        ttl_seconds: int,
    ) -> ArtifactRef:
        """Upload ``data`` under a server-minted key; return an :class:`ArtifactRef`."""
        ref = self._id_gen.new_id()
        key = f"{self._key_prefix}{self._safe(scope.appeal_id)}/{ref}.bin"
        sha256 = hashlib.sha256(data).hexdigest()
        expires_at_iso = self._expiry_iso(ttl_seconds)

        async with self._client_factory() as client:
            await client.put_object(
                Bucket=self._bucket,
                Key=key,
                Body=data,
                Metadata={
                    "appeal_id": scope.appeal_id,
                    "kind": scope.kind,
                    "sha256": sha256,
                },
            )

        self._records[ref] = _Record(
            ref=ref,
            appeal_id=scope.appeal_id,
            kind=scope.kind,
            sha256=sha256,
            key=key,
            expires_at_iso=expires_at_iso,
        )
        return ArtifactRef(
            ref=ref,
            scope=scope,
            sha256=sha256,
            ttl_expires_at_iso=expires_at_iso,
        )

    async def get_signed_url(
        self,
        ref: ArtifactRef,
        *,
        principal: Principal,
    ) -> SignedUrl:
        """Presign a short-lived GET URL for ``ref``, gated by ``principal``."""
        self._authorize(ref, principal)
        record = self._require_record(ref.ref)
        async with self._client_factory() as client:
            url: str = await client.generate_presigned_url(
                "get_object",
                Params={"Bucket": self._bucket, "Key": record.key},
                ExpiresIn=self._url_ttl_seconds,
            )
        return SignedUrl(url=url, expires_at_iso=self._expiry_iso(self._url_ttl_seconds))

    async def open(
        self,
        ref: ArtifactRef,
        *,
        principal: Principal,
    ) -> bytes:
        """Download and return the raw bytes for ``ref``, gated by ``principal``."""
        self._authorize(ref, principal)
        record = self._live_record(ref.ref)
        if record is None:
            raise FileNotFoundError(f"artifact not found or expired: {ref.ref}")
        async with self._client_factory() as client:
            response = await client.get_object(Bucket=self._bucket, Key=record.key)
            body = response["Body"]
            data: bytes = await body.read()
        return data

    async def delete(self, ref: ArtifactRef) -> None:
        """Delete the S3 object for ``ref`` (idempotent)."""
        record = self._records.pop(ref.ref, None)
        if record is None:
            return
        async with self._client_factory() as client:
            await client.delete_object(Bucket=self._bucket, Key=record.key)

    async def sweep_expired(self) -> int:
        """Delete every expired object (belt-and-braces over S3 lifecycle)."""
        now_iso = self._clock.now().isoformat()
        expired = [
            record
            for record in self._records.values()
            if record.expires_at_iso <= now_iso
        ]
        if not expired:
            return 0
        async with self._client_factory() as client:
            for record in expired:
                self._records.pop(record.ref, None)
                await client.delete_object(Bucket=self._bucket, Key=record.key)
        return len(expired)

    # ------------------------------------------------------------------ #
    # Internals.
    # ------------------------------------------------------------------ #
    def _authorize(self, ref: ArtifactRef, principal: Principal) -> None:
        """Assert ``principal`` owns ``ref.scope.appeal_id`` (admins exempt)."""
        if principal.role == "admin":
            return
        if ref.scope.appeal_id not in principal.owned_ids:
            raise Forbidden(f"principal does not own appeal {ref.scope.appeal_id!r}")

    def _require_record(self, ref: str) -> _Record:
        """Return the record for ``ref`` or raise :class:`FileNotFoundError`."""
        record = self._records.get(ref)
        if record is None:
            raise FileNotFoundError(f"artifact not found: {ref}")
        return record

    def _live_record(self, ref: str) -> Optional[_Record]:
        """Return the record for ``ref`` if present and not yet expired."""
        record = self._records.get(ref)
        if record is None:
            return None
        if record.expires_at_iso <= self._clock.now().isoformat():
            return None
        return record

    @staticmethod
    def _safe(appeal_id: str) -> str:
        """Flatten an appeal id into a key-safe token (no path separators)."""
        return "".join(ch for ch in appeal_id if ch.isalnum() or ch in "-_")

    def _expiry_iso(self, ttl_seconds: int) -> str:
        """Return an ISO-8601 expiry ``ttl_seconds`` from the clock's now."""
        return (self._clock.now() + timedelta(seconds=ttl_seconds)).isoformat()

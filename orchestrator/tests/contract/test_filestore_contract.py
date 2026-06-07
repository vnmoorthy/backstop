"""Contract suite for :class:`FileStorePort` (local jail + S3 presigned).

Both concrete adapters are instantiated and asserted to honour the same port.
The local adapter exercises the security properties end-to-end on a real temp
directory; the S3 adapter is driven against a fully in-memory fake S3 client (no
network, no boto3 import required) so a missing SDK never blocks the gate.

Load-bearing M13 assertions:

* a forged ref filename cannot escape the storage jail (path traversal);
* an expired artifact is swept and no longer readable (TTL);
* a principal that does not own the scope is refused (per-appeal ownership);
* the returned handle is always an opaque ref, never a filesystem path.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any, Dict

import pytest

from backstop.adapters.filestore.local_filestore_adapter import LocalFileStoreAdapter
from backstop.adapters.filestore.s3_filestore_adapter import S3FileStoreAdapter
from backstop.adapters.system.uuid_id_gen_adapter import UuidIdGenAdapter
from backstop.domain.errors import Forbidden
from backstop.ports.auth_port import Principal
from backstop.ports.file_store_port import ArtifactRef, ArtifactScope, FileStorePort


class _ManualClock:
    """A controllable clock; ``advance`` moves wall-clock time forward."""

    def __init__(self) -> None:
        self._now = dt.datetime(2026, 6, 7, 12, 0, 0, tzinfo=dt.timezone.utc)

    def now(self) -> dt.datetime:
        return self._now

    def monotonic(self) -> float:
        return self._now.timestamp()

    def advance(self, seconds: float) -> None:
        self._now = self._now + dt.timedelta(seconds=seconds)


def _owner(appeal_id: str) -> Principal:
    return Principal(subject="nurse-1", role="nurse", owned_ids=frozenset({appeal_id}))


def _stranger() -> Principal:
    return Principal(subject="nurse-2", role="nurse", owned_ids=frozenset({"other"}))


# --------------------------------------------------------------------------- #
# Fake S3 client so the S3 adapter is testable with no boto3 / network.
# --------------------------------------------------------------------------- #
class _FakeS3Client:
    """An in-memory stand-in for an aioboto3 S3 client context manager."""

    def __init__(self, store: Dict[str, bytes]) -> None:
        self._store = store

    async def __aenter__(self) -> _FakeS3Client:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    # boto3's S3 API uses PascalCase kwargs; mirror it so the adapter's calls
    # bind correctly (hence the N803 suppressions).
    async def put_object(self, *, Bucket: str, Key: str, Body: bytes, **_: Any) -> None:  # noqa: N803
        self._store[Key] = Body

    async def get_object(self, *, Bucket: str, Key: str) -> Dict[str, Any]:  # noqa: N803
        if Key not in self._store:
            raise FileNotFoundError(Key)
        return {"Body": _FakeBody(self._store[Key])}

    async def delete_object(self, *, Bucket: str, Key: str) -> None:  # noqa: N803
        self._store.pop(Key, None)

    async def generate_presigned_url(self, _op: str, *, Params: Dict[str, str], ExpiresIn: int) -> str:  # noqa: N803, E501
        return f"https://s3.example/{Params['Key']}?X-Expires={ExpiresIn}"


class _FakeBody:
    """Minimal async body matching the ``response['Body'].read()`` shape."""

    def __init__(self, data: bytes) -> None:
        self._data = data

    async def read(self) -> bytes:
        return self._data


def _make_local(tmp_path: Path, clock: _ManualClock) -> LocalFileStoreAdapter:
    return LocalFileStoreAdapter(
        root=tmp_path / "jail",
        secret="test-signing-secret",
        id_gen=UuidIdGenAdapter(),
        clock=clock,
    )


def _make_s3(clock: _ManualClock) -> S3FileStoreAdapter:
    store: Dict[str, bytes] = {}

    def factory() -> _FakeS3Client:
        return _FakeS3Client(store)

    return S3FileStoreAdapter(
        bucket="test-bucket",
        client_factory=factory,
        id_gen=UuidIdGenAdapter(),
        clock=clock,
    )


@pytest.fixture(params=["local", "s3"])
def store(request: pytest.FixtureRequest, tmp_path: Path) -> FileStorePort:
    """Yield each concrete file store so every shared test runs against both."""
    clock = _ManualClock()
    request.node.clock = clock  # type: ignore[attr-defined]
    if request.param == "local":
        return _make_local(tmp_path, clock)
    return _make_s3(clock)


def test_both_stores_satisfy_the_port(tmp_path: Path) -> None:
    """Both concrete adapters expose every method the port declares."""
    clock = _ManualClock()
    local = _make_local(tmp_path, clock)
    s3 = _make_s3(clock)
    for adapter in (local, s3):
        for method in ("put", "get_signed_url", "open", "delete", "sweep_expired"):
            assert callable(getattr(adapter, method))


async def test_put_returns_opaque_ref_not_path(store: FileStorePort) -> None:
    """``put`` returns an :class:`ArtifactRef` whose ref is not a filesystem path."""
    ref = await store.put(b"hello", scope=ArtifactScope("ap-1", "appeal_letter"), ttl_seconds=60)
    assert isinstance(ref, ArtifactRef)
    assert "/" not in ref.ref and "\\" not in ref.ref
    assert ref.scope.appeal_id == "ap-1"


async def test_owner_can_read_stranger_cannot(store: FileStorePort) -> None:
    """The owning principal reads the bytes; a stranger is refused (ownership)."""
    ref = await store.put(
        b"secret-bytes", scope=ArtifactScope("ap-1", "appeal_letter"), ttl_seconds=60
    )
    assert await store.open(ref, principal=_owner("ap-1")) == b"secret-bytes"
    with pytest.raises(Forbidden):
        await store.open(ref, principal=_stranger())


async def test_signed_url_requires_ownership(store: FileStorePort) -> None:
    """Minting a signed URL is gated by per-appeal ownership."""
    ref = await store.put(b"x", scope=ArtifactScope("ap-1", "appeal_letter"), ttl_seconds=60)
    signed = await store.get_signed_url(ref, principal=_owner("ap-1"))
    assert signed.url
    with pytest.raises(Forbidden):
        await store.get_signed_url(ref, principal=_stranger())


async def test_ttl_sweep_reclaims_expired(
    store: FileStorePort, request: pytest.FixtureRequest
) -> None:
    """An artifact past its TTL is swept and then unreadable."""
    clock: _ManualClock = request.node.clock  # type: ignore[attr-defined]
    ref = await store.put(b"y", scope=ArtifactScope("ap-1", "appeal_letter"), ttl_seconds=30)
    clock.advance(31.0)
    reclaimed = await store.sweep_expired()
    assert reclaimed == 1
    with pytest.raises(FileNotFoundError):
        await store.open(ref, principal=_owner("ap-1"))


async def test_delete_is_idempotent(store: FileStorePort) -> None:
    """Deleting an artifact twice is a no-op the second time."""
    ref = await store.put(b"z", scope=ArtifactScope("ap-1", "appeal_letter"), ttl_seconds=60)
    await store.delete(ref)
    await store.delete(ref)  # no raise
    with pytest.raises(FileNotFoundError):
        await store.open(ref, principal=_owner("ap-1"))


# --------------------------------------------------------------------------- #
# Local-only: path-jail traversal defence (the S3 adapter has no on-disk path).
# --------------------------------------------------------------------------- #
async def test_local_store_rejects_path_traversal(tmp_path: Path) -> None:
    """A forged ref whose filename encodes ``../`` cannot escape the jail."""
    clock = _ManualClock()
    adapter = _make_local(tmp_path, clock)
    real = await adapter.put(b"ok", scope=ArtifactScope("ap-1", "appeal_letter"), ttl_seconds=60)
    # Forge a record pointing at a traversal filename, then attempt a read.
    adapter._records[real.ref] = type(adapter._records[real.ref])(
        ref=real.ref,
        appeal_id="ap-1",
        kind="appeal_letter",
        sha256=real.sha256,
        filename="../../../../etc/passwd",
        expires_at_iso=real.ttl_expires_at_iso,
    )
    with pytest.raises(Forbidden):
        await adapter.open(real, principal=_owner("ap-1"))


async def test_local_signed_token_round_trip_and_expiry(tmp_path: Path) -> None:
    """The signed token verifies for the owner and is rejected once expired."""
    clock = _ManualClock()
    adapter = _make_local(tmp_path, clock)
    ref = await adapter.put(b"ok", scope=ArtifactScope("ap-1", "appeal_letter"), ttl_seconds=600)
    signed = await adapter.get_signed_url(ref, principal=_owner("ap-1"))
    # Pull the token out of the minted URL and verify it.
    token = signed.url.rsplit("token=", 1)[1]
    assert adapter.verify_token(ref.ref, signed.expires_at_iso, token)
    # A tampered token is rejected.
    assert not adapter.verify_token(ref.ref, signed.expires_at_iso, "deadbeef")
    # Once past expiry the token no longer verifies.
    clock.advance(10_000.0)
    assert not adapter.verify_token(ref.ref, signed.expires_at_iso, token)

"""File store port: scoped, TTL-bounded artifact storage behind a signed ref.

Defines the :class:`FileStorePort` protocol plus its request/result DTOs.
Artifacts (rendered appeal-letter PDFs, parsed denial images) are stored as
opaque bytes and handed back as an :class:`ArtifactRef` -- never a filesystem
path -- so no user-controlled path ever reaches the store. Reads are gated by a
principal (per-appeal ownership authz) and short-lived signed tokens, and every
artifact carries a TTL swept by :meth:`FileStorePort.sweep_expired`.

Implemented by ``LocalFileStoreAdapter`` (path-jail + signed-token + TTL sweeper)
and ``S3FileStoreAdapter`` (presigned URLs + lifecycle expiry). This module
imports only :mod:`backstop.domain`; it performs no I/O and imports no vendor SDK.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from backstop.ports.auth_port import Principal


@dataclass(frozen=True)
class ArtifactScope:
    """Ownership/visibility scope an artifact is filed under.

    ``appeal_id`` ties the artifact to one appeal aggregate; ``kind`` is a short
    classifier (e.g. ``"appeal_letter"``, ``"denial_image"``) used for lifecycle
    and authz decisions. The scope, not a path, is the unit of access control.
    """

    appeal_id: str
    kind: str


@dataclass(frozen=True)
class ArtifactRef:
    """Opaque handle to a stored artifact (never a filesystem path).

    ``ref`` is the storage-internal id; ``scope`` records ownership; ``sha256``
    is the content digest for integrity checks; ``ttl_expires_at_iso`` is the
    ISO-8601 expiry after which the sweeper reclaims the bytes.
    """

    ref: str
    scope: ArtifactScope
    sha256: str
    ttl_expires_at_iso: str


@dataclass(frozen=True)
class SignedUrl:
    """A short-lived, signed URL granting time-boxed read access to an artifact.

    ``url`` is the access URL; ``expires_at_iso`` is its ISO-8601 expiry. The
    token embedded in ``url`` is bound to the artifact ref and must not outlive
    ``expires_at_iso``.
    """

    url: str
    expires_at_iso: str


@runtime_checkable
class FileStorePort(Protocol):
    """Storage port for scoped, TTL-bounded, principal-gated artifact bytes.

    Returns refs (not paths); reads require a principal for ownership authz.
    Services name this protocol and never the concrete adapter.
    """

    async def put(
        self,
        data: bytes,
        *,
        scope: ArtifactScope,
        ttl_seconds: int,
    ) -> ArtifactRef:
        """Store ``data`` under ``scope`` with a ``ttl_seconds`` lifetime.

        Returns an :class:`ArtifactRef` -- never a path. Implementations
        canonicalise/jail any internal naming so the returned ref cannot encode
        a traversal. The artifact is reclaimed once its TTL elapses.
        """
        ...

    async def get_signed_url(
        self,
        ref: ArtifactRef,
        *,
        principal: Principal,
    ) -> SignedUrl:
        """Mint a short-lived signed URL for ``ref``, gated by ``principal``.

        Enforces per-appeal ownership authz against ``ref.scope`` before
        minting. Raises :class:`backstop.domain.errors.Forbidden` when the
        principal does not own the scope.
        """
        ...

    async def open(
        self,
        ref: ArtifactRef,
        *,
        principal: Principal,
    ) -> bytes:
        """Return the raw bytes for ``ref``, gated by ``principal``.

        Enforces ownership authz against ``ref.scope``. Raises
        :class:`backstop.domain.errors.Forbidden` on an authz failure. Expired
        or unknown refs are treated as not found by the implementation.
        """
        ...

    async def delete(self, ref: ArtifactRef) -> None:
        """Delete the artifact identified by ``ref`` (idempotent).

        Removing an already-absent ref is a no-op rather than an error.
        """
        ...

    async def sweep_expired(self) -> int:
        """Reclaim every artifact whose TTL has elapsed.

        Deletes expired bytes and their bookkeeping rows. Returns the number of
        artifacts reclaimed.
        """
        ...

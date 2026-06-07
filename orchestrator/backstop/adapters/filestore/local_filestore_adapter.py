"""Path-jailed local file-store adapter for :class:`FileStorePort`.

Implements :class:`backstop.ports.file_store_port.FileStorePort` over a single
jail directory. Three security properties are load-bearing and closed the
audited *arbitrary file read / path traversal* and *unauthenticated /files*
findings:

* **Path jail** -- the store *generates* every on-disk name from a fresh server
  id; a user-controlled path or scope value never becomes a filesystem path. The
  resolved path is asserted to live inside the jail before any read/write, so a
  ``../../etc/passwd`` ref cannot escape.
* **Signed token** -- :meth:`get_signed_url` mints a short-lived HMAC token bound
  to the artifact ref and expiry; the URL is unguessable and time-boxed.
* **TTL sweep** -- every artifact carries a TTL; :meth:`sweep_expired` reclaims
  the bytes (and bookkeeping) of anything whose deadline has passed, read off the
  injected clock.

Reads are gated by a :class:`~backstop.ports.auth_port.Principal`: a caller may
only open an artifact whose ``scope.appeal_id`` it owns. This adapter imports
only the standard library plus :mod:`backstop.domain`/:mod:`backstop.ports`; it
imports no vendor SDK.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Dict, Optional
from urllib.parse import quote

from backstop.domain.errors import Forbidden
from backstop.ports.auth_port import Principal
from backstop.ports.clock_port import ClockPort
from backstop.ports.file_store_port import ArtifactRef, ArtifactScope, SignedUrl
from backstop.ports.id_gen_port import IdGenPort

__all__ = ["LocalFileStoreAdapter"]


@dataclass(frozen=True)
class _Record:
    """Internal bookkeeping for one stored artifact."""

    ref: str
    appeal_id: str
    kind: str
    sha256: str
    filename: str
    expires_at_iso: str


class LocalFileStoreAdapter:
    """Local, path-jailed :class:`~backstop.ports.file_store_port.FileStorePort`.

    Construction injects the jail ``root`` directory, a signing ``secret`` for
    URL tokens, the id generator (for server-minted names) and the clock (for
    TTL math). All artifact bytes live directly under ``root`` with
    server-generated filenames.
    """

    def __init__(
        self,
        *,
        root: Path,
        secret: str,
        id_gen: IdGenPort,
        clock: ClockPort,
        url_ttl_seconds: int = 300,
    ) -> None:
        """Create the jail directory and store injected collaborators."""
        self._root = root.resolve()
        self._root.mkdir(parents=True, exist_ok=True)
        self._secret = secret.encode("utf-8")
        self._id_gen = id_gen
        self._clock = clock
        self._url_ttl_seconds = url_ttl_seconds
        self._records: Dict[str, _Record] = {}

    async def put(
        self,
        data: bytes,
        *,
        scope: ArtifactScope,
        ttl_seconds: int,
    ) -> ArtifactRef:
        """Store ``data`` under ``scope`` with a ``ttl_seconds`` lifetime.

        The on-disk filename is server-generated (never derived from ``scope``),
        so the returned ref cannot encode a traversal. Returns an
        :class:`ArtifactRef`.
        """
        ref = self._id_gen.new_id()
        # Server-minted filename: random + ref, sanitised to a flat token. No
        # part of it comes from caller-controlled input.
        filename = f"{ref}-{secrets.token_hex(8)}.bin"
        target = self._jailed_path(filename)
        target.write_bytes(data)

        sha256 = hashlib.sha256(data).hexdigest()
        expires_at_iso = self._expiry_iso(ttl_seconds)
        self._records[ref] = _Record(
            ref=ref,
            appeal_id=scope.appeal_id,
            kind=scope.kind,
            sha256=sha256,
            filename=filename,
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
        """Mint a short-lived signed URL for ``ref``, gated by ``principal``.

        Enforces per-appeal ownership against ``ref.scope`` before minting and
        raises :class:`Forbidden` when the principal does not own the scope.
        """
        self._authorize(ref, principal)
        expires_at_iso = self._expiry_iso(self._url_ttl_seconds)
        token = self._sign(ref.ref, expires_at_iso)
        url = (
            f"/files/{quote(ref.ref, safe='')}"
            f"?expires={quote(expires_at_iso, safe='')}&token={token}"
        )
        return SignedUrl(url=url, expires_at_iso=expires_at_iso)

    async def open(
        self,
        ref: ArtifactRef,
        *,
        principal: Principal,
    ) -> bytes:
        """Return the raw bytes for ``ref``, gated by ``principal``.

        Enforces ownership authz against ``ref.scope`` and raises
        :class:`Forbidden` on failure. Expired or unknown refs are treated as not
        found (raising :class:`FileNotFoundError`).
        """
        self._authorize(ref, principal)
        record = self._live_record(ref.ref)
        if record is None:
            raise FileNotFoundError(f"artifact not found or expired: {ref.ref}")
        return self._jailed_path(record.filename).read_bytes()

    async def read_signed(self, ref: str, expires_at_iso: str, token: str) -> bytes:
        """Return the bytes for ``ref`` iff ``token`` is a live signature.

        The signed token is itself the capability: it is minted by
        :meth:`get_signed_url` only after a per-appeal ownership check, so a valid
        token proves the holder was authorised. The controller serving ``/files``
        calls this — it never reconstructs a principal from an unauthenticated
        request. A forged or expired token raises :class:`Forbidden`; an
        unknown/expired ref raises :class:`FileNotFoundError`.
        """
        if not self.verify_token(ref, expires_at_iso, token):
            raise Forbidden("invalid or expired file token")
        record = self._live_record(ref)
        if record is None:
            raise FileNotFoundError(f"artifact not found or expired: {ref}")
        return self._jailed_path(record.filename).read_bytes()

    async def delete(self, ref: ArtifactRef) -> None:
        """Delete the artifact identified by ``ref`` (idempotent)."""
        record = self._records.pop(ref.ref, None)
        if record is None:
            return
        self._unlink(record.filename)

    async def sweep_expired(self) -> int:
        """Reclaim every artifact whose TTL has elapsed; return the count."""
        now_iso = self._clock.now().isoformat()
        expired = [
            record
            for record in self._records.values()
            if record.expires_at_iso <= now_iso
        ]
        for record in expired:
            self._records.pop(record.ref, None)
            self._unlink(record.filename)
        return len(expired)

    # ------------------------------------------------------------------ #
    # Internals.
    # ------------------------------------------------------------------ #
    def _authorize(self, ref: ArtifactRef, principal: Principal) -> None:
        """Assert ``principal`` owns ``ref.scope.appeal_id`` (admins exempt)."""
        if principal.role == "admin":
            return
        if ref.scope.appeal_id not in principal.owned_ids:
            raise Forbidden(
                f"principal does not own appeal {ref.scope.appeal_id!r}"
            )

    def _jailed_path(self, filename: str) -> Path:
        """Resolve ``filename`` under the jail, refusing any escape.

        Even though filenames are server-generated, the resolved path is
        re-checked against the jail root so a crafted/forged ref can never read
        outside the directory (defence in depth on the traversal finding).
        """
        candidate = (self._root / filename).resolve()
        try:
            candidate.relative_to(self._root)
        except ValueError as exc:
            raise Forbidden("artifact path escapes the storage jail") from exc
        return candidate

    def _live_record(self, ref: str) -> Optional[_Record]:
        """Return the record for ``ref`` if present and not yet expired."""
        record = self._records.get(ref)
        if record is None:
            return None
        if record.expires_at_iso <= self._clock.now().isoformat():
            return None
        return record

    def _unlink(self, filename: str) -> None:
        """Remove the backing file for ``filename`` (missing-ok)."""
        self._jailed_path(filename).unlink(missing_ok=True)

    def _sign(self, ref: str, expires_at_iso: str) -> str:
        """Return the hex HMAC-SHA256 token binding ``ref`` to ``expires_at_iso``."""
        message = f"{ref}:{expires_at_iso}".encode()
        return hmac.new(self._secret, message, hashlib.sha256).hexdigest()

    def verify_token(self, ref: str, expires_at_iso: str, token: str) -> bool:
        """Constant-time check that ``token`` is a live signature for ``ref``.

        The controller calls this when serving ``/files``: it returns ``False``
        for a forged token *or* one whose ``expires_at_iso`` has passed.
        """
        if expires_at_iso <= self._clock.now().isoformat():
            return False
        expected = self._sign(ref, expires_at_iso)
        return hmac.compare_digest(expected, token)

    def _expiry_iso(self, ttl_seconds: int) -> str:
        """Return an ISO-8601 expiry ``ttl_seconds`` from the clock's now."""
        return (self._clock.now() + timedelta(seconds=ttl_seconds)).isoformat()

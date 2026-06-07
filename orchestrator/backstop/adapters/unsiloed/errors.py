"""Typed adapter errors for the Unsiloed denial-parser adapters.

These descend from :class:`~backstop.domain.errors.BackstopError` so the
ingestion service can catch the domain base type at the port boundary while
still pattern-matching on the specific failure. They carry no PHI and no secret
material: the only context they record is an opaque vendor job id or HTTP status,
never the artifact bytes, the member id, or the ``api-key``.

``UnsupportedArtifact`` is the one the real adapter raises to *delegate*: when an
artifact kind cannot be served by Unsiloed (raw EDI), the service treats it as a
deterministic signal to fall back to the sim adapter rather than an outage.
"""

from __future__ import annotations

from typing import Optional

from backstop.domain.enums import ArtifactKind
from backstop.domain.errors import BackstopError

__all__ = [
    "UnsiloedError",
    "UnsupportedArtifact",
    "UnsiloedAuthError",
    "UnsiloedTimeout",
    "UnsiloedJobFailed",
]


class UnsiloedError(BackstopError):
    """Base class for every failure raised by the Unsiloed adapters."""


class UnsupportedArtifact(UnsiloedError):  # noqa: N818 - delegation signal, not an -Error
    """Raised when an adapter cannot parse the requested :class:`ArtifactKind`.

    The real Unsiloed adapter raises this for raw EDI kinds so the ingestion
    service falls back to the deterministic parser; EDI therefore never touches
    the network.
    """

    def __init__(self, kind: ArtifactKind) -> None:
        """Record the unsupported ``kind`` (an enum member, never PHI)."""
        super().__init__(f"unsupported artifact kind: {kind.value}")
        self.kind = kind


class UnsiloedAuthError(UnsiloedError):
    """Raised when Unsiloed rejects the credential (HTTP 401/403).

    The offending key is never embedded in the message — only the status.
    """

    def __init__(self, status_code: Optional[int] = None) -> None:
        """Record the rejecting HTTP ``status_code`` (no secret material)."""
        detail = f" (status {status_code})" if status_code is not None else ""
        super().__init__(f"unsiloed authentication failed{detail}")
        self.status_code = status_code


class UnsiloedTimeout(UnsiloedError):  # noqa: N818 - vendor-named, parallels CapacityTimeout
    """Raised when a create/poll cycle exceeds its bounded retry budget."""

    def __init__(self, job_id: Optional[str] = None) -> None:
        """Record the opaque ``job_id`` whose polling timed out, if known."""
        detail = f" for job {job_id}" if job_id else ""
        super().__init__(f"unsiloed extract timed out{detail}")
        self.job_id = job_id


class UnsiloedJobFailed(UnsiloedError):  # noqa: N818 - vendor job-state name, not an -Error
    """Raised when Unsiloed reports a terminal failure status for the job."""

    def __init__(self, job_id: Optional[str] = None, status: Optional[str] = None) -> None:
        """Record the opaque ``job_id`` and terminal ``status`` string."""
        parts = []
        if job_id:
            parts.append(f"job {job_id}")
        if status:
            parts.append(f"status {status}")
        suffix = f" ({', '.join(parts)})" if parts else ""
        super().__init__(f"unsiloed extract job failed{suffix}")
        self.job_id = job_id
        self.status = status

"""Domain error hierarchy for the Backstop appeals kernel.

Every failure the domain or its adapters can raise descends from
:class:`BackstopError`, so callers can catch the base type at a boundary while
still pattern-matching on specific subclasses. These are pure exception types:
no I/O, no vendor imports, no logging. They carry only the minimal context a
caller needs to render a safe (PHI-free) message.
"""

from __future__ import annotations

from typing import Optional

__all__ = [
    "BackstopError",
    "AppealNotFound",
    "SignoffConflict",
    "RedactionError",
    "RetrievalError",
    "CapacityTimeout",
    "ChannelNotFound",
    "Unauthenticated",
    "Forbidden",
    "AppealStateError",
]


class BackstopError(Exception):
    """Root of every Backstop domain error.

    Catching this type at a boundary guarantees the failure originated inside
    the application rather than from an unexpected runtime fault.
    """


class AppealNotFound(BackstopError):
    """Raised when an appeal id does not resolve to a stored aggregate."""

    def __init__(self, appeal_id: str) -> None:
        """Record the missing ``appeal_id`` (an opaque, non-PHI identifier)."""
        super().__init__(f"appeal not found: {appeal_id}")
        self.appeal_id = appeal_id


class SignoffConflict(BackstopError):
    """Raised when a sign-off cannot be applied because of a state conflict.

    Examples: another nurse already holds the claim, the appeal moved on, or
    the letter hash the signature covers no longer matches.
    """


class RedactionError(BackstopError):
    """Raised when PHI redaction fails or unredacted PHI reaches a boundary.

    Treated as a hard failure: it is never safe to fall back to emitting the
    raw text.
    """


class RetrievalError(BackstopError):
    """Raised when an evidence/runbook retrieval cannot be completed."""


class CapacityTimeout(BackstopError):
    """Raised when a bounded-capacity resource waits past its deadline unserved."""


class ChannelNotFound(BackstopError):
    """Raised when an event-bus channel cannot be resolved for publish/subscribe."""

    def __init__(self, channel: str) -> None:
        """Record the unresolved ``channel`` name."""
        super().__init__(f"channel not found: {channel}")
        self.channel = channel


class Unauthenticated(BackstopError):
    """Raised when a request carries no valid credential (HTTP 401 analogue)."""


class Forbidden(BackstopError):
    """Raised when an authenticated principal lacks rights (HTTP 403 analogue)."""


class AppealStateError(BackstopError):
    """Raised when an event is illegal for an appeal's current status.

    Emitted by the pure transition function in
    :mod:`backstop.domain.appeal_aggregate` and surfaces the offending
    ``from_status`` / ``event`` pair for diagnostics.
    """

    def __init__(
        self,
        message: str,
        *,
        from_status: Optional[str] = None,
        event: Optional[str] = None,
    ) -> None:
        """Build the error with optional transition context."""
        super().__init__(message)
        self.from_status = from_status
        self.event = event

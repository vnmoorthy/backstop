"""Event bus port: redaction-typed fan-out to dashboard / WebSocket subscribers.

Defines the :class:`EventBusPort` protocol plus its event DTO. This is a PHI
*egress* port: events leave the trust boundary to browser dashboards, so the
event payload is structurally typed :class:`backstop.domain.redacted.RedactedText`.
Because ``RedactedText`` is produced *only* by the redaction port, a raw ``str``
on the wire is a type error at the boundary -- the PHI-over-WebSocket class of
vulnerability becomes uncompilable rather than caught at runtime.

Implemented by ``WsEventBusAdapter`` (real WS fan-out and in-memory sim). This
module imports only :mod:`backstop.domain`; it performs no I/O and imports no
vendor SDK.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import AsyncIterator, Protocol, runtime_checkable

from backstop.domain.redacted import RedactedText
from backstop.ports.auth_port import Principal


@dataclass(frozen=True)
class RedactedEvent:
    """A single redacted timeline event published to a channel.

    ``kind`` is the event discriminator (e.g. ``"line_composed"``) and is safe,
    non-PHI metadata. ``body`` is the only free-text field and is typed
    :class:`RedactedText` so PHI cannot reach a subscriber: it can only have been
    produced through the redaction port. ``seq_iso`` is an ISO-8601 ordering
    timestamp from the injected clock.
    """

    kind: str
    body: RedactedText
    seq_iso: str


@runtime_checkable
class EventBusPort(Protocol):
    """Pub/sub egress port for redacted, per-channel timeline events.

    Every published payload is :class:`RedactedText`-typed, so unredacted PHI
    cannot be published. Services name this protocol and never the concrete
    adapter.
    """

    async def publish(self, channel: str, event: RedactedEvent) -> None:
        """Fan ``event`` out to all subscribers of ``channel``.

        Accepts only a :class:`RedactedEvent` whose free-text ``body`` is
        :class:`RedactedText`; a raw ``str`` payload is rejected by the type
        system at the call site. Implementations additionally assert PHI-clean
        at runtime as defence-in-depth.
        """
        ...

    def subscribe(
        self,
        channel: str,
        principal: Principal,
    ) -> AsyncIterator[RedactedEvent]:
        """Return an async iterator of redacted events for ``channel``.

        The ``principal`` is authorised for the channel before any event is
        delivered. Each subscriber gets its own back-pressured stream; a slow
        consumer cannot stall publishers or other subscribers. Raises
        :class:`backstop.domain.errors.Forbidden` when the principal may not
        read the channel.
        """
        ...

    async def close(self) -> None:
        """Close the bus and drop every subscriber stream (idempotent)."""
        ...

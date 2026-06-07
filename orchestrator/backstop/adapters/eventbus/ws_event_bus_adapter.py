"""In-process, RedactedText-only pub/sub adapter for :class:`EventBusPort`.

Implements :class:`backstop.ports.event_bus_port.EventBusPort`. This is a PHI
*egress* port: events leave the trust boundary to browser dashboards over a
WebSocket, so the event ``body`` is structurally typed
:class:`~backstop.domain.redacted.RedactedText`. The same in-process fan-out
backs both the real WebSocket controller (each browser socket drains one
subscription queue) and the test sim -- there is no separate echo implementation.

Design points:

* **RedactedText-only** -- :meth:`publish` rejects any event whose ``body`` is not
  a genuine ``RedactedText`` (defence-in-depth at runtime, on top of the
  compile-time type), raising :class:`~backstop.domain.errors.RedactionError`.
* **Per-subscriber back-pressure** -- each subscription owns a bounded
  :class:`asyncio.Queue`; a slow consumer drops its own oldest events rather than
  stalling publishers or other subscribers.
* **Channel authz** -- :meth:`subscribe` checks the principal against the channel
  (per-appeal ownership) before yielding any event.

This module imports only :mod:`backstop.domain`/:mod:`backstop.ports` and the
standard library; it performs no I/O and imports no vendor SDK.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import AsyncIterator, Dict, Set

from backstop.domain.errors import Forbidden, RedactionError
from backstop.domain.redacted import RedactedText
from backstop.ports.auth_port import Principal
from backstop.ports.event_bus_port import EventBusPort, RedactedEvent

__all__ = ["WsEventBusAdapter"]

# Default per-subscriber queue depth. Beyond this a slow consumer sheds its own
# oldest events so it can never apply back-pressure to a publisher.
_DEFAULT_QUEUE_MAXSIZE = 256

# Channels every authenticated principal may read (non-PHI, broadcast metadata).
_PUBLIC_CHANNELS: Set[str] = {"system", "health"}

# Channel-name prefix that ties a channel to a specific appeal id, e.g.
# ``appeal:ap-123``. Only the owning principal (or an admin) may subscribe.
_APPEAL_CHANNEL_PREFIX = "appeal:"


class _Subscription:
    """One subscriber's bounded, drop-oldest event queue."""

    __slots__ = ("queue",)

    def __init__(self, maxsize: int) -> None:
        """Create the bounded queue backing this subscription."""
        self.queue: asyncio.Queue[RedactedEvent] = asyncio.Queue(maxsize=maxsize)

    def offer(self, event: RedactedEvent) -> None:
        """Enqueue ``event``, dropping the oldest first if the queue is full."""
        if self.queue.full():
            with contextlib.suppress(asyncio.QueueEmpty):  # pragma: no cover - race-only
                self.queue.get_nowait()
        self.queue.put_nowait(event)


class WsEventBusAdapter(EventBusPort):
    """RedactedText-only :class:`EventBusPort` with per-subscriber back-pressure.

    A single instance is shared process-wide; the WebSocket controller calls
    :meth:`subscribe` per browser socket and :meth:`publish` from services.
    """

    def __init__(self, *, queue_maxsize: int = _DEFAULT_QUEUE_MAXSIZE) -> None:
        """Start with no channels and no subscribers."""
        self._channels: Dict[str, Set[_Subscription]] = {}
        self._queue_maxsize = queue_maxsize
        self._closed = False

    async def publish(self, channel: str, event: RedactedEvent) -> None:
        """Fan ``event`` out to every subscriber of ``channel``.

        Rejects a non-:class:`RedactedText` ``body`` at runtime (defence in depth)
        and is a no-op once the bus is closed or the channel has no subscribers.
        """
        if not isinstance(event.body, RedactedText):
            raise RedactionError(
                "EventBus.publish requires a RedactedText body; raw text is refused"
            )
        if self._closed:
            return
        for subscription in tuple(self._channels.get(channel, ())):
            subscription.offer(event)

    def subscribe(
        self,
        channel: str,
        principal: Principal,
    ) -> AsyncIterator[RedactedEvent]:
        """Return an async iterator of redacted events for ``channel``.

        Authorises ``principal`` for ``channel`` (per-appeal ownership) *before*
        returning the stream, raising :class:`Forbidden` on denial. Each
        subscriber gets its own back-pressured queue.
        """
        self._authorize_channel(channel, principal)
        subscription = _Subscription(self._queue_maxsize)
        self._channels.setdefault(channel, set()).add(subscription)
        return self._stream(channel, subscription)

    async def close(self) -> None:
        """Close the bus and drop every subscriber stream (idempotent)."""
        self._closed = True
        self._channels.clear()

    async def _stream(
        self,
        channel: str,
        subscription: _Subscription,
    ) -> AsyncIterator[RedactedEvent]:
        """Yield queued events until the bus closes or the consumer detaches."""
        try:
            while not self._closed:
                event = await subscription.queue.get()
                yield event
        finally:
            subscribers = self._channels.get(channel)
            if subscribers is not None:
                subscribers.discard(subscription)
                if not subscribers:
                    self._channels.pop(channel, None)

    @staticmethod
    def _authorize_channel(channel: str, principal: Principal) -> None:
        """Assert ``principal`` may read ``channel``; raise :class:`Forbidden`.

        Public channels are open to any authenticated principal. An
        ``appeal:<id>`` channel is readable only by an ``admin`` or the principal
        that owns ``<id>``.
        """
        if channel in _PUBLIC_CHANNELS:
            return
        if principal.role == "admin":
            return
        if channel.startswith(_APPEAL_CHANNEL_PREFIX):
            appeal_id = channel[len(_APPEAL_CHANNEL_PREFIX) :]
            if appeal_id in principal.owned_ids:
                return
        raise Forbidden(f"principal may not read channel {channel!r}")

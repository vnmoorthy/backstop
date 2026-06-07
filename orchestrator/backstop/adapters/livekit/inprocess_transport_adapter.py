"""In-process :class:`VoiceTransportPort` simulation (a real asyncio fabric).

This is the ``sim`` adapter for the voice transport port. It does **genuine**
local work rather than echoing strings: every open channel owns a real
:class:`asyncio.Queue`-backed publish/subscribe fabric so agent and nurse
participants can exchange frames inside the process exactly as they would across
a LiveKit room, and tokens are real HS256 JWTs minted by the shared
:mod:`backstop.adapters.livekit._tokens` crypto (so the sim passes the *same*
token contract as the real adapter).

The fabric is fully asyncio-native:

* :meth:`publish` fans a frame out to every *other* subscriber of a room.
* :meth:`subscribe` yields an async iterator of frames for one participant.

Room lifecycle (open/close) is idempotent and bookkept in-memory, so closing an
already-closed (or never-opened) channel is a no-op — which is exactly the leak
fix the contract test pins down.
"""

from __future__ import annotations

import asyncio
import datetime as _dt
from dataclasses import dataclass, field
from datetime import timedelta
from typing import AsyncIterator, Callable, Dict, List, Optional

from backstop.adapters.livekit._tokens import decode_token, encode_token
from backstop.domain.enums import SpecialistKind
from backstop.domain.errors import ChannelNotFound
from backstop.ports.voice_transport_port import (
    DEFAULT_CHANNEL_TTL,
    DEFAULT_NURSE_TTL,
    Channel,
    Claims,
    JoinToken,
    Participant,
)

__all__ = ["InProcessTransportAdapter", "Frame"]

# Identity assigned to the agent participant minted at ``open_channel`` time.
_AGENT_IDENTITY = "agent"


@dataclass(frozen=True)
class Frame:
    """One media/data frame published into a room's fabric.

    Attributes:
        sender: Identity of the publishing participant.
        payload: Opaque frame bytes (non-PHI in the sim; real audio in prod).
        seq: Monotonic per-room sequence number, assigned on publish.
    """

    sender: str
    payload: bytes
    seq: int


@dataclass
class _Subscriber:
    """A single participant's inbound frame queue within a room."""

    identity: str
    queue: asyncio.Queue[Frame] = field(default_factory=asyncio.Queue)


@dataclass
class _Room:
    """In-memory state for one open channel and its pub/sub fabric."""

    call_id: str
    room_name: str
    expires_at: _dt.datetime
    max_participants: int
    metadata: Dict[str, str]
    participants: Dict[str, Participant] = field(default_factory=dict)
    subscribers: Dict[str, _Subscriber] = field(default_factory=dict)
    _seq: int = 0

    def next_seq(self) -> int:
        """Return the next monotonic frame sequence number for this room."""
        self._seq += 1
        return self._seq


class InProcessTransportAdapter:
    """A real in-process asyncio pub/sub transport honouring the port.

    Tokens are genuine HS256 JWTs; rooms and participants are tracked in memory;
    frames flow through per-participant :class:`asyncio.Queue`s. No network and
    no vendor SDK are touched on any path.
    """

    def __init__(
        self,
        *,
        api_key: str = "sim-livekit",
        secret: str = "dev-livekit-sim-secret",  # noqa: S107 - non-secret sim default
        clock: Optional[Callable[[], _dt.datetime]] = None,
    ) -> None:
        """Build the sim transport.

        Args:
            api_key: Issuer (``iss``) stamped on every minted token.
            secret: HMAC signing secret for the shared HS256 mint/verify.
            clock: Injectable ``now`` provider (defaults to UTC wall clock) so
                TTL/expiry math stays deterministic under test.
        """
        self._api_key = api_key
        self._secret = secret
        self._clock = clock if clock is not None else _utc_now
        self._rooms: Dict[str, _Room] = {}
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------ #
    # Channel lifecycle.
    # ------------------------------------------------------------------ #
    async def open_channel(
        self,
        call_id: str,
        *,
        max_participants: int = 4,
        ttl: timedelta = DEFAULT_CHANNEL_TTL,
        metadata: Optional[Dict[str, str]] = None,
    ) -> Channel:
        """Open a room for *call_id* and mint the agent join token (idempotent)."""
        async with self._lock:
            now = self._clock()
            room_name = f"call-{call_id}"
            expires_at = now + ttl
            room = self._rooms.get(call_id)
            if room is None:
                room = _Room(
                    call_id=call_id,
                    room_name=room_name,
                    expires_at=expires_at,
                    max_participants=max_participants,
                    metadata=dict(metadata or {}),
                )
                self._rooms[call_id] = room
                self._join(room, _AGENT_IDENTITY, SpecialistKind.PROVIDER_LINE, now)
            agent_token = self._mint(
                room.room_name, _AGENT_IDENTITY, now, ttl, True, True
            )
            return Channel(
                call_id=call_id,
                room_name=room.room_name,
                agent_token=agent_token.token,
                expires_at=room.expires_at,
                max_participants=room.max_participants,
                metadata=dict(room.metadata),
            )

    async def close_channel(self, call_id: str) -> None:
        """Tear down *call_id*'s room and drain its fabric; idempotent."""
        async with self._lock:
            room = self._rooms.pop(call_id, None)
            if room is None:
                return  # Already closed / never opened — a no-op, not an error.
            for subscriber in room.subscribers.values():
                # Unblock any pending subscribe() iterators by sentinel close.
                _drain(subscriber.queue)
            room.participants.clear()
            room.subscribers.clear()

    # ------------------------------------------------------------------ #
    # Token minting.
    # ------------------------------------------------------------------ #
    def mint_join_token(
        self,
        room_name: str,
        identity: str,
        *,
        ttl: timedelta = DEFAULT_CHANNEL_TTL,
        can_publish: bool = True,
        can_subscribe: bool = True,
    ) -> JoinToken:
        """Mint a room-scoped HS256 join token (pure local crypto, no I/O)."""
        return self._mint(room_name, identity, self._clock(), ttl, can_publish, can_subscribe)

    async def bridge_nurse(
        self,
        call_id: str,
        nurse_identity: str,
        *,
        ttl: timedelta = DEFAULT_NURSE_TTL,
    ) -> JoinToken:
        """Mint a short-TTL barge-in token and register the nurse participant."""
        async with self._lock:
            room = self._rooms.get(call_id)
            if room is None:
                raise ChannelNotFound(call_id)
            now = self._clock()
            self._join(room, nurse_identity, SpecialistKind.RECORDS_DESK, now)
            return self._mint(room.room_name, nurse_identity, now, ttl, True, True)

    # ------------------------------------------------------------------ #
    # Participants.
    # ------------------------------------------------------------------ #
    async def list_participants(self, call_id: str) -> List[Participant]:
        """List participants currently present in *call_id*'s room."""
        async with self._lock:
            room = self._rooms.get(call_id)
            if room is None:
                raise ChannelNotFound(call_id)
            return sorted(room.participants.values(), key=lambda p: p.joined_at)

    async def remove_participant(self, call_id: str, identity: str) -> None:
        """Eject *identity* from *call_id*'s room (idempotent for the identity)."""
        async with self._lock:
            room = self._rooms.get(call_id)
            if room is None:
                raise ChannelNotFound(call_id)
            room.participants.pop(identity, None)
            subscriber = room.subscribers.pop(identity, None)
            if subscriber is not None:
                _drain(subscriber.queue)

    # ------------------------------------------------------------------ #
    # The pub/sub frame fabric (the genuine local work).
    # ------------------------------------------------------------------ #
    async def publish(self, call_id: str, sender: str, payload: bytes) -> int:
        """Fan *payload* from *sender* out to every other subscriber of the room.

        Returns:
            The monotonic per-room sequence number assigned to the frame.

        Raises:
            ChannelNotFound: If no open channel backs *call_id*.
        """
        async with self._lock:
            room = self._rooms.get(call_id)
            if room is None:
                raise ChannelNotFound(call_id)
            seq = room.next_seq()
            frame = Frame(sender=sender, payload=payload, seq=seq)
            targets = [
                sub.queue for ident, sub in room.subscribers.items() if ident != sender
            ]
        # Enqueue outside the lock so a slow consumer never blocks the fabric.
        for queue in targets:
            queue.put_nowait(frame)
        return seq

    async def subscribe(self, call_id: str, identity: str) -> AsyncIterator[Frame]:
        """Yield frames addressed to *identity* until the channel closes.

        Registers a per-participant inbound queue and returns an async iterator
        draining it. The iterator ends when the room is closed or the
        participant is removed.

        Raises:
            ChannelNotFound: If no open channel backs *call_id*.
        """
        async with self._lock:
            room = self._rooms.get(call_id)
            if room is None:
                raise ChannelNotFound(call_id)
            subscriber = room.subscribers.get(identity)
            if subscriber is None:
                subscriber = _Subscriber(identity=identity)
                room.subscribers[identity] = subscriber
            queue = subscriber.queue
        return self._iterate(call_id, identity, queue)

    async def _iterate(
        self, call_id: str, identity: str, queue: asyncio.Queue[Frame]
    ) -> AsyncIterator[Frame]:
        """Drain *queue* while the room and participant remain registered."""
        while True:
            if not self._is_subscribed(call_id, identity):
                return
            try:
                frame = await asyncio.wait_for(queue.get(), timeout=0.05)
            except asyncio.TimeoutError:
                continue
            yield frame

    def _is_subscribed(self, call_id: str, identity: str) -> bool:
        """Return ``True`` while *identity* still has a live subscription."""
        room = self._rooms.get(call_id)
        return room is not None and identity in room.subscribers

    # ------------------------------------------------------------------ #
    # Token contract.
    # ------------------------------------------------------------------ #
    def verify_token(self, token: str) -> Claims:
        """Verify *token* under the sim secret and return its decoded claims."""
        grant = decode_token(token, secret=self._secret, now=self._clock())
        return Claims(
            issuer=grant.issuer,
            subject=grant.subject,
            room_name=grant.room_name,
            expires_at=grant.expires_at,
            can_publish=grant.can_publish,
            can_subscribe=grant.can_subscribe,
        )

    async def aclose(self) -> None:
        """Gracefully release all rooms and their fabrics."""
        async with self._lock:
            for room in self._rooms.values():
                for subscriber in room.subscribers.values():
                    _drain(subscriber.queue)
            self._rooms.clear()

    # ------------------------------------------------------------------ #
    # Internals.
    # ------------------------------------------------------------------ #
    def _mint(
        self,
        room_name: str,
        identity: str,
        now: _dt.datetime,
        ttl: timedelta,
        can_publish: bool,
        can_subscribe: bool,
    ) -> JoinToken:
        """Mint one HS256 join token via the shared crypto module."""
        expires_at = now + ttl
        token = encode_token(
            api_key=self._api_key,
            secret=self._secret,
            identity=identity,
            room_name=room_name,
            issued_at=now,
            expires_at=expires_at,
            can_publish=can_publish,
            can_subscribe=can_subscribe,
        )
        return JoinToken(
            token=token,
            identity=identity,
            room_name=room_name,
            expires_at=expires_at,
        )

    @staticmethod
    def _join(
        room: _Room, identity: str, kind: SpecialistKind, now: _dt.datetime
    ) -> None:
        """Register *identity* as a participant of *room* if not already present."""
        if identity not in room.participants:
            room.participants[identity] = Participant(
                identity=identity, kind=kind, joined_at=now
            )
        if identity not in room.subscribers:
            room.subscribers[identity] = _Subscriber(identity=identity)


def _utc_now() -> _dt.datetime:
    """Return the current timezone-aware UTC instant (default clock)."""
    return _dt.datetime.now(tz=_dt.timezone.utc)


def _drain(queue: asyncio.Queue[Frame]) -> None:
    """Discard any buffered frames so a closed room releases its memory."""
    while not queue.empty():
        try:
            queue.get_nowait()
        except asyncio.QueueEmpty:  # pragma: no cover - race with concurrent get
            break

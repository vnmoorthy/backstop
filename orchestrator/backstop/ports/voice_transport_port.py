"""VoiceTransportPort — LiveKit per-call room transport (L2 port).

Provides one room per call with short-TTL, room-scoped HS256 join tokens (minting
is pure local crypto, no I/O) and a ``bridge_nurse`` barge-in path. Token TTLs are
short to bound PHI exposure (15 m agent / 5 m nurse). ``verify_token`` runs at the
WS edge during the handshake. The sim adapter is a real in-process asyncio pub/sub
fabric with real HS256 tokens passing the same token contract.

This module defines the Protocol plus its request/result DTOs only; concrete
adapters live in ``backstop.adapters.livekit``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Protocol, runtime_checkable

from backstop.domain.enums import SpecialistKind

__all__ = [
    "Channel",
    "JoinToken",
    "Participant",
    "Claims",
    "VoiceTransportPort",
]

#: Default per-call channel time-to-live before the room is torn down.
DEFAULT_CHANNEL_TTL = timedelta(minutes=15)

#: Default nurse barge-in token time-to-live (shorter, to bound exposure).
DEFAULT_NURSE_TTL = timedelta(minutes=5)


@dataclass(frozen=True)
class Channel:
    """A per-call transport room with its agent join token and expiry.

    Attributes:
        call_id: The call this room backs.
        room_name: The transport room identifier.
        agent_token: The minted agent join token (room-scoped HS256).
        expires_at: Absolute room expiry timestamp.
        max_participants: Maximum simultaneous participants allowed.
        metadata: Free-form non-PHI room metadata.
    """

    call_id: str
    room_name: str
    agent_token: str
    expires_at: datetime
    max_participants: int = 4
    metadata: Dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class JoinToken:
    """A minted, room-scoped join token with its expiry and identity.

    Attributes:
        token: The encoded HS256 JWT.
        identity: The participant identity the token authorizes.
        room_name: The room the grant is scoped to.
        expires_at: Absolute token expiry timestamp.
    """

    token: str
    identity: str
    room_name: str
    expires_at: datetime


@dataclass(frozen=True)
class Participant:
    """One participant currently present in a channel.

    Attributes:
        identity: The participant's stable identity.
        kind: The specialist line/role this participant represents.
        joined_at: When the participant joined the room.
    """

    identity: str
    kind: SpecialistKind
    joined_at: datetime


@dataclass(frozen=True)
class Claims:
    """Decoded, verified claims from a join token.

    Attributes:
        issuer: Token issuer (the transport API key).
        subject: Token subject (the participant identity).
        room_name: The room the grant is scoped to.
        expires_at: Absolute token expiry timestamp.
        can_publish: Whether the grant permits publishing media.
        can_subscribe: Whether the grant permits subscribing to media.
    """

    issuer: str
    subject: str
    room_name: str
    expires_at: datetime
    can_publish: bool
    can_subscribe: bool


@runtime_checkable
class VoiceTransportPort(Protocol):
    """Per-call room transport with short-TTL, room-scoped HS256 tokens."""

    async def open_channel(
        self,
        call_id: str,
        *,
        max_participants: int = 4,
        ttl: timedelta = DEFAULT_CHANNEL_TTL,
        metadata: Optional[Dict[str, str]] = None,
    ) -> Channel:
        """Open one room for ``call_id`` and mint the agent join token."""
        ...

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
        ...

    async def bridge_nurse(
        self,
        call_id: str,
        nurse_identity: str,
        *,
        ttl: timedelta = DEFAULT_NURSE_TTL,
    ) -> JoinToken:
        """Mint a short-TTL barge-in token for a nurse joining ``call_id``.

        Raises:
            ChannelNotFound: If no open channel backs ``call_id``.
        """
        ...

    async def list_participants(self, call_id: str) -> List[Participant]:
        """List participants currently present in ``call_id``'s room.

        Raises:
            ChannelNotFound: If no open channel backs ``call_id``.
        """
        ...

    async def remove_participant(self, call_id: str, identity: str) -> None:
        """Eject ``identity`` from ``call_id``'s room.

        Raises:
            ChannelNotFound: If no open channel backs ``call_id``.
        """
        ...

    async def close_channel(self, call_id: str) -> None:
        """Tear down ``call_id``'s room; idempotent (no error if absent)."""
        ...

    def verify_token(self, token: str) -> Claims:
        """Verify ``token`` and return its decoded claims.

        Raises:
            Unauthenticated: If the token is invalid, tampered, or expired.
        """
        ...

    async def aclose(self) -> None:
        """Gracefully release all transport resources."""
        ...

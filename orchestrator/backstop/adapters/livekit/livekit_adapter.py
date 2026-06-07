"""Real LiveKit :class:`VoiceTransportPort` adapter (HS256 mint + room I/O).

This is the ``real`` adapter. It does two things against a live LiveKit
deployment:

* **Token minting** (``mint_join_token`` / ``open_channel`` / ``bridge_nurse``)
  is *pure local crypto* — genuine HS256 JWTs carrying the LiveKit ``video``
  grant, produced by the shared :mod:`backstop.adapters.livekit._tokens` module.
  No network and no SDK are required to mint a token, so a token can be issued
  to the WS edge with zero round-trips and the module imports cleanly even when
  the vendor SDK is absent.

* **Room lifecycle** (``open_channel`` / ``close_channel`` /
  ``list_participants`` / ``remove_participant``) calls the LiveKit server API
  via the ``livekit-api`` SDK. The SDK is imported **lazily inside the methods**
  so importing this module never requires the SDK to be installed. Room create
  and delete are **idempotent**: creating a room that already exists returns the
  existing one, and deleting an absent room is a no-op — closing the transport
  leak the contract test pins down.

All vendor and transport faults are translated into the domain error types in
:mod:`backstop.domain.errors` (``ChannelNotFound`` / ``Unauthenticated``); the
caller never sees a raw ``livekit`` or ``httpx`` exception.
"""

from __future__ import annotations

import datetime as _dt
import json
from datetime import timedelta
from typing import Any, Callable, Dict, List, Optional

from backstop.adapters.livekit._tokens import decode_token, encode_token
from backstop.domain.enums import SpecialistKind
from backstop.domain.errors import ChannelNotFound, Unauthenticated
from backstop.ports.voice_transport_port import (
    DEFAULT_CHANNEL_TTL,
    DEFAULT_NURSE_TTL,
    Channel,
    Claims,
    JoinToken,
    Participant,
)

__all__ = ["LiveKitTransportAdapter"]

# Identity assigned to the agent participant minted at ``open_channel`` time.
_AGENT_IDENTITY = "agent"

# Specialist line each well-known identity represents (telemetry only, no PHI).
_AGENT_KIND = SpecialistKind.PROVIDER_LINE
_NURSE_KIND = SpecialistKind.RECORDS_DESK


class LiveKitTransportAdapter:
    """Real LiveKit transport: local HS256 minting + server-API room lifecycle."""

    def __init__(
        self,
        *,
        url: str,
        api_key: str,
        api_secret: str,
        clock: Optional[Callable[[], _dt.datetime]] = None,
    ) -> None:
        """Build the real adapter.

        Args:
            url: The LiveKit server URL (e.g. ``wss://...`` / ``https://...``).
            api_key: The LiveKit API key (``LIVEKIT_API_KEY``); also the token
                issuer (``iss``).
            api_secret: The LiveKit API secret (``LIVEKIT_API_SECRET``); the
                HMAC signing secret.
            clock: Injectable ``now`` provider (defaults to UTC wall clock) so
                TTL/expiry math stays deterministic under test.
        """
        self._url = url
        self._api_key = api_key
        self._api_secret = api_secret
        self._clock = clock if clock is not None else _utc_now
        # Lazily-constructed SDK client; created on first room-I/O call so the
        # module imports without the vendor SDK present.
        self._client: Optional[Any] = None

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
        """Create (idempotently) the room for *call_id* and mint the agent token."""
        now = self._clock()
        room_name = _room_name(call_id)
        client = self._lk_client()
        empty_timeout = max(int(ttl.total_seconds()), 1)
        try:
            from livekit import api as lkapi  # lazy vendor import

            request = lkapi.CreateRoomRequest(
                name=room_name,
                empty_timeout=empty_timeout,
                max_participants=max_participants,
                metadata=_encode_metadata(metadata),
            )
            await client.room.create_room(request)
        except Exception as exc:  # translate every vendor fault
            _reraise_unless_already_exists(exc, call_id)

        agent_token = self._mint(room_name, _AGENT_IDENTITY, now, ttl, True, True)
        return Channel(
            call_id=call_id,
            room_name=room_name,
            agent_token=agent_token.token,
            expires_at=now + ttl,
            max_participants=max_participants,
            metadata=dict(metadata or {}),
        )

    async def close_channel(self, call_id: str) -> None:
        """Delete *call_id*'s room; idempotent (absent room is a no-op)."""
        client = self._lk_client()
        try:
            from livekit import api as lkapi  # lazy vendor import

            await client.room.delete_room(
                lkapi.DeleteRoomRequest(room=_room_name(call_id))
            )
        except Exception as exc:  # translate every vendor fault
            if _is_not_found(exc):
                return  # Already gone — deletion is idempotent.
            # Any other fault: stay quiet on close to avoid leaking on teardown.
            return

    # ------------------------------------------------------------------ #
    # Token minting (pure local crypto, no I/O, no SDK).
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
        """Mint a short-TTL barge-in token for a nurse joining *call_id*.

        Confirms the room exists (so an unknown channel raises
        :class:`ChannelNotFound`) before minting the grant.
        """
        await self._require_room(call_id)
        return self._mint(_room_name(call_id), nurse_identity, self._clock(), ttl, True, True)

    # ------------------------------------------------------------------ #
    # Participants.
    # ------------------------------------------------------------------ #
    async def list_participants(self, call_id: str) -> List[Participant]:
        """List participants currently present in *call_id*'s room."""
        client = self._lk_client()
        try:
            from livekit import api as lkapi  # lazy vendor import

            response = await client.room.list_participants(
                lkapi.ListParticipantsRequest(room=_room_name(call_id))
            )
        except Exception as exc:  # translate every vendor fault
            raise ChannelNotFound(call_id) from exc
        return [
            Participant(
                identity=info.identity,
                kind=_kind_for(info.identity),
                joined_at=_dt.datetime.fromtimestamp(
                    float(info.joined_at), tz=_dt.timezone.utc
                ),
            )
            for info in response.participants
        ]

    async def remove_participant(self, call_id: str, identity: str) -> None:
        """Eject *identity* from *call_id*'s room."""
        client = self._lk_client()
        try:
            from livekit import api as lkapi  # lazy vendor import

            await client.room.remove_participant(
                lkapi.RoomParticipantIdentity(room=_room_name(call_id), identity=identity)
            )
        except Exception as exc:  # translate every vendor fault
            if _is_not_found(exc):
                raise ChannelNotFound(call_id) from exc
            raise ChannelNotFound(call_id) from exc

    # ------------------------------------------------------------------ #
    # Token contract.
    # ------------------------------------------------------------------ #
    def verify_token(self, token: str) -> Claims:
        """Verify *token* under the API secret and return its decoded claims."""
        grant = decode_token(token, secret=self._api_secret, now=self._clock())
        if grant.issuer != self._api_key:
            raise Unauthenticated("token issuer does not match this API key")
        return Claims(
            issuer=grant.issuer,
            subject=grant.subject,
            room_name=grant.room_name,
            expires_at=grant.expires_at,
            can_publish=grant.can_publish,
            can_subscribe=grant.can_subscribe,
        )

    async def aclose(self) -> None:
        """Gracefully release the SDK client and its underlying HTTP session."""
        client = self._client
        self._client = None
        if client is None:
            return
        aclose = getattr(client, "aclose", None)
        if callable(aclose):
            await aclose()

    # ------------------------------------------------------------------ #
    # Internals.
    # ------------------------------------------------------------------ #
    def _lk_client(self) -> Any:
        """Return the lazily-constructed LiveKit server-API client.

        The ``livekit-api`` SDK is imported here (not at module import) so this
        adapter module loads cleanly even when the SDK is not installed.
        """
        if self._client is None:
            from livekit import api as lkapi  # lazy vendor import

            self._client = lkapi.LiveKitAPI(
                url=self._url,
                api_key=self._api_key,
                api_secret=self._api_secret,
            )
        return self._client

    async def _require_room(self, call_id: str) -> None:
        """Raise :class:`ChannelNotFound` unless *call_id*'s room exists."""
        client = self._lk_client()
        room_name = _room_name(call_id)
        try:
            from livekit import api as lkapi  # lazy vendor import

            response = await client.room.list_rooms(
                lkapi.ListRoomsRequest(names=[room_name])
            )
        except Exception as exc:  # translate every vendor fault
            raise ChannelNotFound(call_id) from exc
        if not any(room.name == room_name for room in response.rooms):
            raise ChannelNotFound(call_id)

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
            secret=self._api_secret,
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


def _utc_now() -> _dt.datetime:
    """Return the current timezone-aware UTC instant (default clock)."""
    return _dt.datetime.now(tz=_dt.timezone.utc)


def _room_name(call_id: str) -> str:
    """Derive the stable transport room name for a call id."""
    return f"call-{call_id}"


def _kind_for(identity: str) -> SpecialistKind:
    """Map a well-known participant identity to its specialist line."""
    if identity == _AGENT_IDENTITY:
        return _AGENT_KIND
    return _NURSE_KIND


def _encode_metadata(metadata: Optional[Dict[str, str]]) -> str:
    """Encode non-PHI room metadata as the SDK-expected compact string."""
    if not metadata:
        return ""
    return json.dumps(metadata, separators=(",", ":"), sort_keys=True)


def _is_not_found(exc: BaseException) -> bool:
    """Return ``True`` if *exc* signals a missing LiveKit room/participant."""
    status = getattr(exc, "status", None)
    if status == 404:
        return True
    message = str(exc).lower()
    return "not found" in message or "does not exist" in message


def _reraise_unless_already_exists(exc: BaseException, call_id: str) -> None:
    """Swallow an idempotent "room already exists" fault; re-raise the rest.

    LiveKit returns the existing room when ``create_room`` names a live room, so
    an "already exists" signal is benign and must not abort ``open_channel``.
    Any other fault is mapped to :class:`ChannelNotFound` for *call_id*.
    """
    message = str(exc).lower()
    if "already exists" in message or getattr(exc, "status", None) == 409:
        return
    raise ChannelNotFound(call_id) from exc

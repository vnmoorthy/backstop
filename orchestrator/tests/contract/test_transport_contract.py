"""Contract suite for :class:`VoiceTransportPort` (real + sim adapters).

Both adapters are exercised through the *same* parametrized fixture and must
honour the identical port behaviour:

* ``open_channel`` returns a :class:`Channel` with a room, an agent token, and a
  **future** ``expires_at``.
* ``bridge_nurse`` on an unknown channel raises the domain ``ChannelNotFound``.
* ``close_channel`` is idempotent (calling it twice — or on an unopened call —
  never raises): the transport-leak fix.
* The minted JWT verifies and decodes to the expected grant claims (room,
  issuer, publish/subscribe rights), and the sim's pub/sub fabric does real
  work.

The real :class:`LiveKitTransportAdapter` must NEVER hit the network here. The
``livekit-api`` SDK is imported lazily inside the adapter's room-I/O methods, so
this module installs a fully in-memory fake ``livekit.api`` into ``sys.modules``
before those methods run. A missing real SDK therefore does not block the gate.
"""

from __future__ import annotations

import asyncio
import datetime as _dt
import sys
import types
from dataclasses import dataclass
from datetime import timedelta
from typing import ClassVar, Dict, Iterator, List, Optional

import pytest

from backstop.adapters.livekit._tokens import decode_token
from backstop.adapters.livekit.inprocess_transport_adapter import (
    InProcessTransportAdapter,
)
from backstop.adapters.livekit.livekit_adapter import LiveKitTransportAdapter
from backstop.domain.errors import ChannelNotFound, Unauthenticated
from backstop.ports.voice_transport_port import (
    Channel,
    Claims,
    JoinToken,
    VoiceTransportPort,
)

_FROZEN_NOW = _dt.datetime(2026, 6, 7, 12, 0, 0, tzinfo=_dt.timezone.utc)
_REAL_KEY = "APIxxxxxxxx"
_REAL_SECRET = "real-livekit-secret-value"
_SIM_SECRET = "dev-livekit-sim-secret"


def _frozen_clock() -> _dt.datetime:
    """Return a fixed UTC instant so TTL/expiry assertions are exact."""
    return _FROZEN_NOW


# --------------------------------------------------------------------------- #
# In-memory fake of the ``livekit.api`` server SDK (no network whatsoever).
# --------------------------------------------------------------------------- #
@dataclass
class _FakeRoom:
    name: str
    metadata: str = ""


@dataclass
class _FakeParticipantInfo:
    identity: str
    joined_at: float


@dataclass
class _FakeListRoomsResponse:
    rooms: List[_FakeRoom]


@dataclass
class _FakeListParticipantsResponse:
    participants: List[_FakeParticipantInfo]


@dataclass
class _CreateRoomRequest:
    name: str
    empty_timeout: int = 0
    max_participants: int = 0
    metadata: str = ""


@dataclass
class _DeleteRoomRequest:
    room: str


@dataclass
class _ListRoomsRequest:
    names: Optional[List[str]] = None


@dataclass
class _ListParticipantsRequest:
    room: str


@dataclass
class _RoomParticipantIdentity:
    room: str
    identity: str


class _FakeRoomService:
    """In-memory stand-in for ``client.room`` with idempotent semantics."""

    def __init__(self, store: Dict[str, _FakeRoom]) -> None:
        self._store = store

    async def create_room(self, request: _CreateRoomRequest) -> _FakeRoom:
        # Idempotent: return the existing room if already present.
        room = self._store.get(request.name)
        if room is None:
            room = _FakeRoom(name=request.name, metadata=request.metadata)
            self._store[request.name] = room
        return room

    async def delete_room(self, request: _DeleteRoomRequest) -> None:
        if request.room not in self._store:
            raise _FakeTwirpError("room not found", status=404)
        del self._store[request.room]

    async def list_rooms(self, request: _ListRoomsRequest) -> _FakeListRoomsResponse:
        names = request.names
        rooms = [
            r for r in self._store.values() if names is None or r.name in names
        ]
        return _FakeListRoomsResponse(rooms=rooms)

    async def list_participants(
        self, request: _ListParticipantsRequest
    ) -> _FakeListParticipantsResponse:
        if request.room not in self._store:
            raise _FakeTwirpError("room not found", status=404)
        return _FakeListParticipantsResponse(
            participants=[
                _FakeParticipantInfo(identity="agent", joined_at=_FROZEN_NOW.timestamp())
            ]
        )

    async def remove_participant(self, request: _RoomParticipantIdentity) -> None:
        if request.room not in self._store:
            raise _FakeTwirpError("room not found", status=404)


class _FakeTwirpError(Exception):
    """Mimics the vendor SDK's HTTP/Twirp error carrying a ``status`` code."""

    def __init__(self, message: str, *, status: int) -> None:
        super().__init__(message)
        self.status = status


class _FakeLiveKitAPI:
    """In-memory stand-in for ``livekit.api.LiveKitAPI`` (shared room store)."""

    _store: ClassVar[Dict[str, _FakeRoom]] = {}

    def __init__(self, *, url: str, api_key: str, api_secret: str) -> None:
        self.url = url
        self.api_key = api_key
        self.api_secret = api_secret
        self.room = _FakeRoomService(type(self)._store)
        self.closed = False

    async def aclose(self) -> None:
        self.closed = True


def _install_fake_livekit() -> None:
    """Inject a fake ``livekit.api`` module so the lazy import resolves offline."""
    api_module = types.ModuleType("livekit.api")
    api_module.LiveKitAPI = _FakeLiveKitAPI  # type: ignore[attr-defined]
    api_module.CreateRoomRequest = _CreateRoomRequest  # type: ignore[attr-defined]
    api_module.DeleteRoomRequest = _DeleteRoomRequest  # type: ignore[attr-defined]
    api_module.ListRoomsRequest = _ListRoomsRequest  # type: ignore[attr-defined]
    api_module.ListParticipantsRequest = _ListParticipantsRequest  # type: ignore[attr-defined]
    api_module.RoomParticipantIdentity = _RoomParticipantIdentity  # type: ignore[attr-defined]

    livekit_pkg = sys.modules.get("livekit")
    if livekit_pkg is None:
        livekit_pkg = types.ModuleType("livekit")
        sys.modules["livekit"] = livekit_pkg
    livekit_pkg.api = api_module  # type: ignore[attr-defined]
    sys.modules["livekit.api"] = api_module


@pytest.fixture()
def fake_livekit() -> Iterator[None]:
    """Install the in-memory fake SDK for the duration of one test."""
    _FakeLiveKitAPI._store.clear()
    saved_livekit = sys.modules.get("livekit")
    saved_api = sys.modules.get("livekit.api")
    _install_fake_livekit()
    try:
        yield
    finally:
        _FakeLiveKitAPI._store.clear()
        _restore(saved_livekit, "livekit")
        _restore(saved_api, "livekit.api")


def _restore(saved: Optional[types.ModuleType], name: str) -> None:
    """Restore (or remove) a previously-saved ``sys.modules`` entry."""
    if saved is None:
        sys.modules.pop(name, None)
    else:
        sys.modules[name] = saved


# --------------------------------------------------------------------------- #
# Adapter fixtures — both must satisfy the same port.
# --------------------------------------------------------------------------- #
def _make_sim() -> InProcessTransportAdapter:
    return InProcessTransportAdapter(
        api_key="sim-livekit", secret=_SIM_SECRET, clock=_frozen_clock
    )


def _make_real() -> LiveKitTransportAdapter:
    return LiveKitTransportAdapter(
        url="https://example.livekit.cloud",
        api_key=_REAL_KEY,
        api_secret=_REAL_SECRET,
        clock=_frozen_clock,
    )


@pytest.fixture(params=["sim", "real"])
def adapter(request: pytest.FixtureRequest, fake_livekit: None) -> VoiceTransportPort:
    """Yield each transport adapter behind the port type (real SDK faked)."""
    port: VoiceTransportPort = _make_sim() if request.param == "sim" else _make_real()
    return port


# --------------------------------------------------------------------------- #
# Contract tests.
# --------------------------------------------------------------------------- #
def test_both_adapters_satisfy_port(fake_livekit: None) -> None:
    """Both concrete adapters are runtime-checkable instances of the port."""
    assert isinstance(_make_sim(), VoiceTransportPort)
    assert isinstance(_make_real(), VoiceTransportPort)


@pytest.mark.asyncio
async def test_open_channel_returns_channel_with_future_expiry(
    adapter: VoiceTransportPort,
) -> None:
    """open_channel yields a Channel with room, agent token, future expiry."""
    channel = await adapter.open_channel("call-42", ttl=timedelta(minutes=15))
    assert isinstance(channel, Channel)
    assert channel.call_id == "call-42"
    assert channel.room_name
    assert channel.agent_token
    assert channel.expires_at == _FROZEN_NOW + timedelta(minutes=15)
    assert channel.expires_at > _FROZEN_NOW
    await adapter.aclose()


@pytest.mark.asyncio
async def test_open_channel_agent_token_decodes_with_grant_claims(
    request: pytest.FixtureRequest, adapter: VoiceTransportPort
) -> None:
    """The minted agent JWT decodes to the expected room-scoped grant claims."""
    secret = _SIM_SECRET if _param(request) == "sim" else _REAL_SECRET
    channel = await adapter.open_channel("call-7")
    grant = decode_token(channel.agent_token, secret=secret, now=_FROZEN_NOW)
    assert grant.room_name == channel.room_name
    assert grant.subject == "agent"
    assert grant.can_publish is True
    assert grant.can_subscribe is True
    assert grant.expires_at == channel.expires_at

    # And the adapter verifies its own token back into Claims.
    claims = adapter.verify_token(channel.agent_token)
    assert isinstance(claims, Claims)
    assert claims.room_name == channel.room_name
    assert claims.can_publish is True
    await adapter.aclose()


@pytest.mark.asyncio
async def test_mint_join_token_is_room_scoped(
    request: pytest.FixtureRequest, adapter: VoiceTransportPort
) -> None:
    """mint_join_token produces a room-scoped HS256 token (pure, no I/O)."""
    token = adapter.mint_join_token(
        "room-x", "nurse-1", ttl=timedelta(minutes=5), can_publish=False
    )
    assert isinstance(token, JoinToken)
    secret = _SIM_SECRET if _param(request) == "sim" else _REAL_SECRET
    grant = decode_token(token.token, secret=secret, now=_FROZEN_NOW)
    assert grant.room_name == "room-x"
    assert grant.subject == "nurse-1"
    assert grant.can_publish is False
    assert grant.can_subscribe is True
    assert token.expires_at == _FROZEN_NOW + timedelta(minutes=5)


@pytest.mark.asyncio
async def test_bridge_nurse_unknown_channel_raises_channel_not_found(
    adapter: VoiceTransportPort,
) -> None:
    """bridge_nurse on an unopened call raises the domain ChannelNotFound."""
    with pytest.raises(ChannelNotFound):
        await adapter.bridge_nurse("never-opened", "nurse-9")


@pytest.mark.asyncio
async def test_bridge_nurse_known_channel_mints_short_ttl(
    request: pytest.FixtureRequest, adapter: VoiceTransportPort
) -> None:
    """bridge_nurse on an open call mints a short-TTL barge-in token."""
    await adapter.open_channel("call-bridge")
    token = await adapter.bridge_nurse(
        "call-bridge", "nurse-2", ttl=timedelta(minutes=5)
    )
    secret = _SIM_SECRET if _param(request) == "sim" else _REAL_SECRET
    grant = decode_token(token.token, secret=secret, now=_FROZEN_NOW)
    assert grant.subject == "nurse-2"
    assert token.expires_at == _FROZEN_NOW + timedelta(minutes=5)
    await adapter.aclose()


@pytest.mark.asyncio
async def test_close_channel_is_idempotent(adapter: VoiceTransportPort) -> None:
    """close_channel never raises — twice, or on a never-opened call."""
    await adapter.close_channel("not-open")  # no-op, no raise
    await adapter.open_channel("call-close")
    await adapter.close_channel("call-close")
    await adapter.close_channel("call-close")  # second close still a no-op
    await adapter.aclose()


@pytest.mark.asyncio
async def test_open_channel_is_idempotent(adapter: VoiceTransportPort) -> None:
    """Opening the same call twice yields the same room (no duplicate)."""
    first = await adapter.open_channel("call-dup")
    second = await adapter.open_channel("call-dup")
    assert first.room_name == second.room_name
    await adapter.aclose()


@pytest.mark.asyncio
async def test_verify_token_rejects_tampered_token(
    adapter: VoiceTransportPort,
) -> None:
    """A token signed under the wrong secret fails verification."""
    foreign = InProcessTransportAdapter(secret="some-other-secret", clock=_frozen_clock)
    bad = foreign.mint_join_token("room-x", "agent")
    with pytest.raises(Unauthenticated):
        adapter.verify_token(bad.token)
    await adapter.aclose()


# --------------------------------------------------------------------------- #
# Sim-only: the pub/sub fabric does genuine local work (not a string echo).
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_sim_pubsub_fabric_delivers_frames() -> None:
    """The sim's asyncio fabric fans a published frame to other subscribers."""
    sim = _make_sim()
    await sim.open_channel("call-fabric")
    await sim.bridge_nurse("call-fabric", "nurse-listen")

    received: List[bytes] = []

    async def listen() -> None:
        stream = await sim.subscribe("call-fabric", "nurse-listen")
        async for frame in stream:
            received.append(frame.payload)
            return  # one frame is enough for the contract

    listener = asyncio.create_task(listen())
    await asyncio.sleep(0)  # let the subscriber register
    seq = await sim.publish("call-fabric", "agent", b"hello-nurse")
    assert seq == 1
    await asyncio.wait_for(listener, timeout=1.0)
    assert received == [b"hello-nurse"]
    await sim.aclose()


@pytest.mark.asyncio
async def test_sim_list_participants_tracks_agent_and_nurse() -> None:
    """The sim tracks the agent and bridged nurse as real participants."""
    sim = _make_sim()
    await sim.open_channel("call-roster")
    await sim.bridge_nurse("call-roster", "nurse-3")
    roster = await sim.list_participants("call-roster")
    identities = {p.identity for p in roster}
    assert identities == {"agent", "nurse-3"}
    await sim.aclose()


@pytest.mark.asyncio
async def test_real_list_participants_unknown_room_raises() -> None:
    """The real adapter maps a missing room to ChannelNotFound (faked SDK)."""
    saved_livekit = sys.modules.get("livekit")
    saved_api = sys.modules.get("livekit.api")
    _FakeLiveKitAPI._store.clear()
    _install_fake_livekit()
    try:
        real = _make_real()
        with pytest.raises(ChannelNotFound):
            await real.list_participants("ghost")
        await real.aclose()
    finally:
        _FakeLiveKitAPI._store.clear()
        _restore(saved_livekit, "livekit")
        _restore(saved_api, "livekit.api")


# --------------------------------------------------------------------------- #
# Helpers.
# --------------------------------------------------------------------------- #
def _param(request: pytest.FixtureRequest) -> str:
    """Return the active ``adapter`` fixture param ('sim' or 'real')."""
    return str(request.node.callspec.params["adapter"])

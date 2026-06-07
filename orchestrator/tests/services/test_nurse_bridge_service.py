"""Tests for :class:`NurseBridgeService`.

Pins: a nurse is authorized against the call's appeal before any token is
minted; only then is a short-TTL barge-in token issued. An unauthorized nurse
gets ``Forbidden`` and no token is minted.
"""

from __future__ import annotations

import pytest

from backstop.domain.errors import ChannelNotFound, Forbidden
from backstop.ports.auth_port import Principal
from backstop.services.nurse_bridge_service import NurseBridgeService
from tests.services.fakes import FakeAuth, FakeTransport

_NURSE = Principal(subject="nurse-1", role="nurse", owned_ids=frozenset({"appeal-1"}))
_OTHER = Principal(subject="nurse-2", role="nurse", owned_ids=frozenset({"appeal-9"}))


def _service(transport: FakeTransport) -> NurseBridgeService:
    """Build a bridge service over the transport and an ownership auth fake."""
    return NurseBridgeService(transport=transport, auth=FakeAuth())


async def test_authorized_nurse_gets_barge_in_token() -> None:
    """An owning nurse is bridged with a minted join token."""
    transport = FakeTransport()
    await transport.open_channel("call-1")
    service = _service(transport)

    grant = await service.bridge(_NURSE, call_id="call-1", appeal_id="appeal-1")

    assert grant.call_id == "call-1"
    assert grant.token.identity == "nurse-1"
    assert transport.bridge_calls == [("call-1", "nurse-1")]


async def test_unauthorized_nurse_denied_and_no_token_minted() -> None:
    """A nurse who does not own the appeal is denied before any mint."""
    transport = FakeTransport()
    await transport.open_channel("call-1")
    service = _service(transport)

    with pytest.raises(Forbidden):
        await service.bridge(_OTHER, call_id="call-1", appeal_id="appeal-1")

    # Authorization failed first → no bridge token was minted.
    assert transport.bridge_calls == []


async def test_bridge_missing_channel_raises() -> None:
    """An authorized nurse bridging a closed call gets ``ChannelNotFound``."""
    transport = FakeTransport()  # no channel opened
    service = _service(transport)

    with pytest.raises(ChannelNotFound):
        await service.bridge(_NURSE, call_id="call-1", appeal_id="appeal-1")


async def test_participants_requires_authorization() -> None:
    """Listing participants is authorized just like bridging."""
    transport = FakeTransport()
    await transport.open_channel("call-1")
    service = _service(transport)

    # Owner can list.
    assert await service.participants(
        _NURSE, call_id="call-1", appeal_id="appeal-1"
    ) == ()

    # Non-owner is denied.
    with pytest.raises(Forbidden):
        await service.participants(_OTHER, call_id="call-1", appeal_id="appeal-1")

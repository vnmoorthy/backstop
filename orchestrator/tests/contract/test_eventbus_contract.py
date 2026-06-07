"""Contract suite for :class:`EventBusPort` (RedactedText-only WS fan-out).

The single concrete adapter is asserted to honour the port. The load-bearing
M13 assertion: ``publish`` refuses a non-:class:`RedactedText` body at runtime
(defence in depth on top of the compile-time type), so unredacted PHI cannot
leave the trust boundary over the WebSocket. We also exercise channel-level
ownership authz and the back-pressured per-subscriber stream.
"""

from __future__ import annotations

import asyncio
from typing import cast

import pytest

from backstop.adapters.eventbus.ws_event_bus_adapter import WsEventBusAdapter
from backstop.domain.errors import Forbidden, RedactionError
from backstop.domain.redacted import SANCTIONED_TOKEN, RedactedText
from backstop.ports.auth_port import Principal
from backstop.ports.event_bus_port import EventBusPort, RedactedEvent


def _redacted(text: str) -> RedactedText:
    """Mint a :class:`RedactedText` through the sanctioned redaction token."""
    return RedactedText.from_redaction(text, SANCTIONED_TOKEN)


def _owner(appeal_id: str) -> Principal:
    return Principal(subject="nurse-1", role="nurse", owned_ids=frozenset({appeal_id}))


def _stranger() -> Principal:
    return Principal(subject="nurse-2", role="nurse", owned_ids=frozenset({"other"}))


def test_adapter_satisfies_the_port() -> None:
    """The concrete adapter is recognised as the runtime-checkable port."""
    bus = WsEventBusAdapter()
    assert isinstance(bus, EventBusPort)


async def test_publish_delivers_to_subscriber() -> None:
    """A published redacted event reaches an authorised subscriber."""
    bus = WsEventBusAdapter()
    stream = bus.subscribe("appeal:ap-1", _owner("ap-1"))
    await bus.publish(
        "appeal:ap-1",
        RedactedEvent(kind="line_composed", body=_redacted("[NAME] appeal opened"), seq_iso="t0"),
    )
    event = await asyncio.wait_for(stream.__anext__(), timeout=1.0)
    assert event.kind == "line_composed"
    assert str(event.body) == "[NAME] appeal opened"
    await bus.close()


async def test_publish_rejects_non_redacted_body() -> None:
    """A raw ``str`` body is refused at runtime (the PHI-egress guard)."""
    bus = WsEventBusAdapter()
    # Forge an event carrying a raw string where RedactedText is required.
    forged = cast(
        RedactedEvent,
        RedactedEvent.__new__(RedactedEvent),
    )
    object.__setattr__(forged, "kind", "line_composed")
    object.__setattr__(forged, "body", "raw PHI: member W812340099")
    object.__setattr__(forged, "seq_iso", "t0")
    with pytest.raises(RedactionError):
        await bus.publish("appeal:ap-1", forged)
    await bus.close()


async def test_subscribe_enforces_channel_ownership() -> None:
    """A principal may not subscribe to an appeal channel it does not own."""
    bus = WsEventBusAdapter()
    with pytest.raises(Forbidden):
        bus.subscribe("appeal:ap-1", _stranger())
    await bus.close()


async def test_admin_may_subscribe_any_channel() -> None:
    """An admin principal may read any appeal channel (break-glass)."""
    bus = WsEventBusAdapter()
    admin = Principal(subject="admin", role="admin", owned_ids=frozenset())
    stream = bus.subscribe("appeal:ap-9", admin)
    await bus.publish(
        "appeal:ap-9",
        RedactedEvent(kind="x", body=_redacted("[REDACTED]"), seq_iso="t0"),
    )
    event = await asyncio.wait_for(stream.__anext__(), timeout=1.0)
    assert str(event.body) == "[REDACTED]"
    await bus.close()


async def test_public_channel_open_to_any_principal() -> None:
    """A public channel is readable by any authenticated principal."""
    bus = WsEventBusAdapter()
    stream = bus.subscribe("system", _stranger())
    await bus.publish(
        "system", RedactedEvent(kind="health", body=_redacted("ok"), seq_iso="t0")
    )
    event = await asyncio.wait_for(stream.__anext__(), timeout=1.0)
    assert str(event.body) == "ok"
    await bus.close()


async def test_slow_subscriber_does_not_stall_publisher() -> None:
    """A subscriber that never reads sheds its own oldest events (back-pressure)."""
    bus = WsEventBusAdapter(queue_maxsize=4)
    # Subscribe but never consume.
    bus.subscribe("appeal:ap-1", _owner("ap-1"))
    # Publish well beyond the queue depth; publish must not block or raise.
    for i in range(50):
        await bus.publish(
            "appeal:ap-1",
            RedactedEvent(kind="tick", body=_redacted(f"[{i}]"), seq_iso=str(i)),
        )
    await bus.close()


async def test_publish_after_close_is_noop() -> None:
    """Publishing after the bus closes is a silent no-op."""
    bus = WsEventBusAdapter()
    await bus.close()
    await bus.publish(
        "system", RedactedEvent(kind="x", body=_redacted("ok"), seq_iso="t0")
    )

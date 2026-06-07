"""NurseBridgeService — authorized nurse barge-in into a live call.

A nurse can join a live payer call to take over. This service first authorizes
the nurse against the call's appeal (per-appeal ownership / RBAC via
:class:`AuthPort`), then mints a **short-TTL** room-scoped join token through
:meth:`VoiceTransportPort.bridge_nurse`. The short TTL bounds PHI exposure on
the barge-in path. Listing/ejecting participants is likewise authorized.

The service depends only on ports; token minting is pure local crypto inside the
transport adapter, so no network I/O happens in the service itself.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Tuple

from backstop.ports.auth_port import (
    AuthorizationRequest,
    AuthPort,
    Principal,
)
from backstop.ports.voice_transport_port import (
    JoinToken,
    Participant,
    VoiceTransportPort,
)

__all__ = ["BridgeGrant", "NurseBridgeService"]


@dataclass(frozen=True)
class BridgeGrant:
    """A minted barge-in grant for a nurse.

    Attributes:
        call_id: The call the nurse is joining.
        token: The short-TTL, room-scoped join token.
    """

    call_id: str
    token: JoinToken


class NurseBridgeService:
    """Authorize a nurse and bridge them into a live call with a short-TTL token."""

    def __init__(
        self,
        *,
        transport: VoiceTransportPort,
        auth: AuthPort,
    ) -> None:
        """Store the transport and auth ports."""
        self._transport = transport
        self._auth = auth

    async def bridge(
        self,
        principal: Principal,
        *,
        call_id: str,
        appeal_id: str,
    ) -> BridgeGrant:
        """Authorize ``principal`` for ``appeal_id`` then mint a barge-in token.

        Raises:
            Forbidden: If the principal may not bridge this appeal's call.
            ChannelNotFound: If no open channel backs ``call_id``.
        """
        self._auth.authorize(
            principal,
            AuthorizationRequest(
                action="bridge",
                resource="calls",
                resource_id=appeal_id,
            ),
        )
        token = await self._transport.bridge_nurse(call_id, principal.subject)
        return BridgeGrant(call_id=call_id, token=token)

    async def participants(
        self,
        principal: Principal,
        *,
        call_id: str,
        appeal_id: str,
    ) -> Tuple[Participant, ...]:
        """List call participants after authorizing the principal.

        Raises:
            Forbidden: If the principal may not read this appeal's call.
            ChannelNotFound: If no open channel backs ``call_id``.
        """
        self._auth.authorize(
            principal,
            AuthorizationRequest(
                action="read",
                resource="calls",
                resource_id=appeal_id,
            ),
        )
        return tuple(await self._transport.list_participants(call_id))

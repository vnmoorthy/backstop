"""Auth port: authentication and RBAC authorization for every protected edge.

Defines the :class:`AuthPort` protocol plus its principal/decision DTOs. The port
is enforced on every HTTP route and the WebSocket handshake: ``authenticate``
turns a bearer token into a :class:`Principal`, and ``authorize`` gates a
``(action, resource)`` against that principal's role and owned ids (per-appeal
ownership plus role-based access control). Failures surface as domain errors --
vendor/JWT exceptions never escape the port.

Implemented by ``JwtAuthAdapter`` (JWT/HMAC verify + RBAC). This module imports
only :mod:`backstop.domain`; it performs no I/O and imports no vendor SDK.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import FrozenSet, Protocol, runtime_checkable


@dataclass(frozen=True)
class Principal:
    """An authenticated caller's identity and authorization material.

    ``subject`` is the stable principal id; ``role`` is the RBAC role
    (e.g. ``"nurse"``, ``"agent"``, ``"admin"``); ``owned_ids`` is the set of
    resource ids (typically appeal ids) the principal owns, used for per-appeal
    ownership checks. The principal carries no PHI.
    """

    subject: str
    role: str
    owned_ids: FrozenSet[str]


@dataclass(frozen=True)
class AuthorizationRequest:
    """A single authorization question against a principal.

    ``action`` is the verb (e.g. ``"create"``, ``"read"``, ``"sign"``);
    ``resource`` is the resource class (e.g. ``"appeals"``, ``"files"``); and
    ``resource_id`` optionally narrows to a specific instance for ownership
    checks (``None`` for collection-level actions).
    """

    action: str
    resource: str
    resource_id: str


@runtime_checkable
class AuthPort(Protocol):
    """Authentication + RBAC authorization port for the application edge.

    Services and controllers name this protocol and never the concrete adapter.
    """

    def authenticate(self, token: str) -> Principal:
        """Verify ``token`` and return the authenticated :class:`Principal`.

        Raises :class:`backstop.domain.errors.Unauthenticated` for a missing,
        malformed, expired, or badly-signed token. Never raises a vendor/JWT
        exception.
        """
        ...

    def authorize(
        self,
        principal: Principal,
        request: AuthorizationRequest,
    ) -> None:
        """Assert ``principal`` may perform ``request``; raise otherwise.

        Enforces RBAC plus per-appeal ownership (``request.resource_id`` against
        ``principal.owned_ids``). Returns ``None`` on success and raises
        :class:`backstop.domain.errors.Forbidden` when the action is denied.
        """
        ...

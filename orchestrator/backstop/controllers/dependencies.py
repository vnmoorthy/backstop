"""Shared controller dependencies: container access + the auth gate.

Two FastAPI dependencies underpin every route:

* :func:`get_container` reads the immutable :class:`Container` off
  ``request.app.state`` — controllers never build one.
* :func:`get_principal` is the authentication gate. It extracts the bearer
  token, verifies it through the :class:`AuthPort`, and returns the
  :class:`Principal`. EVERY route except the health probes depends on it, so an
  unauthenticated request is rejected with ``401`` before any handler runs.

A small :func:`require_authorized` helper turns a domain :class:`Forbidden` into
a ``403`` for per-resource ownership checks.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional, cast

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from backstop.domain.errors import Forbidden, Unauthenticated
from backstop.ports.auth_port import Principal

if TYPE_CHECKING:  # pragma: no cover - typing only
    from backstop.composition.container import Container
    from backstop.services.auth_service import AuthService

# ``auto_error=False`` so a missing header yields our own 401 (with a clean
# WWW-Authenticate) rather than FastAPI's terse default.
_bearer = HTTPBearer(auto_error=False)


def get_container(request: Request) -> Container:
    """Return the wired :class:`Container` stored on the application state."""
    container = getattr(request.app.state, "container", None)
    if container is None:  # pragma: no cover - defensive; lifespan sets it
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="application not initialized",
        )
    return cast("Container", container)


def get_auth_service(
    container: Container = Depends(get_container),
) -> AuthService:
    """Return the :class:`AuthService` from the container (never ``None``)."""
    service = container.auth_service
    if service is None:  # pragma: no cover - defensive
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="auth not initialized",
        )
    return service


def get_principal(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer),
    auth: AuthService = Depends(get_auth_service),
) -> Principal:
    """Authenticate the bearer token and return the :class:`Principal`.

    Raises:
        HTTPException: ``401`` when the credential is missing or invalid.
    """
    if credentials is None or not credentials.credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="missing bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        return auth.authenticate(credentials.credentials)
    except Unauthenticated as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc


# Roles permitted to read a collection-level worklist/queue (the rows are
# redacted views; per-appeal detail remains ownership-gated below).
_WORKLIST_READER_ROLES = frozenset({"admin", "nurse", "agent"})


def require_authorized(
    auth: AuthService,
    principal: Principal,
    *,
    action: str,
    resource: str,
    resource_id: str,
) -> None:
    """Authorize ``principal`` for ``(action, resource, resource_id)`` or 403.

    Raises:
        HTTPException: ``403`` when RBAC or ownership denies the request.
    """
    try:
        auth.authorize(principal, action, resource, resource_id)
    except Forbidden as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="forbidden",
        ) from exc


def require_worklist_reader(principal: Principal) -> None:
    """Authorize a collection-level worklist/queue read by role (no ownership).

    Worklist rows are redacted views, so any authenticated reviewer role may
    page them; opening a specific appeal still goes through the ownership-gated
    :func:`require_authorized`.

    Raises:
        HTTPException: ``403`` when the principal's role may not read worklists.
    """
    if principal.role not in _WORKLIST_READER_ROLES:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="forbidden",
        )

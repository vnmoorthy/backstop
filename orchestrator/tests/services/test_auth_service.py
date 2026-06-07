"""Tests for :class:`AuthService`.

Pins: authentication resolves a token to a principal (or raises
``Unauthenticated``); authorization enforces RBAC + per-appeal ownership (or
raises ``Forbidden``); and ``check`` composes authn-then-authz.
"""

from __future__ import annotations

import pytest

from backstop.domain.errors import Forbidden, Unauthenticated
from backstop.ports.auth_port import Principal
from backstop.services.auth_service import AuthService
from tests.services.fakes import FakeAuth

_NURSE = Principal(subject="nurse-1", role="nurse", owned_ids=frozenset({"appeal-1"}))
_ADMIN = Principal(subject="admin-1", role="admin", owned_ids=frozenset())


def _service() -> AuthService:
    """Build an auth service with a nurse and admin token."""
    return AuthService(
        FakeAuth({"nurse-token": _NURSE, "admin-token": _ADMIN})
    )


def test_authenticate_resolves_principal() -> None:
    """A valid token yields its principal."""
    assert _service().authenticate("nurse-token") is _NURSE


def test_authenticate_rejects_bad_token() -> None:
    """An unknown token raises ``Unauthenticated``."""
    with pytest.raises(Unauthenticated):
        _service().authenticate("nope")


def test_authorize_allows_owner() -> None:
    """A principal may act on a resource it owns."""
    _service().authorize(_NURSE, "read", "appeals", "appeal-1")  # no raise


def test_authorize_denies_cross_appeal_access() -> None:
    """A principal may not act on an appeal it does not own."""
    with pytest.raises(Forbidden):
        _service().authorize(_NURSE, "read", "appeals", "appeal-999")


def test_admin_bypasses_ownership() -> None:
    """An admin may act on any resource."""
    _service().authorize(_ADMIN, "sign", "appeals", "appeal-1")  # no raise


def test_check_composes_authn_then_authz() -> None:
    """``check`` authenticates then authorizes, returning the principal."""
    principal = _service().check("nurse-token", "read", "appeals", "appeal-1")
    assert principal is _NURSE


def test_check_raises_forbidden_after_authn() -> None:
    """A valid token but unowned resource raises ``Forbidden`` from ``check``."""
    with pytest.raises(Forbidden):
        _service().check("nurse-token", "read", "appeals", "appeal-2")


def test_principal_owns_helper() -> None:
    """``principal_owns`` reflects the principal's owned ids."""
    service = _service()
    assert service.principal_owns(_NURSE, "appeal-1") is True
    assert service.principal_owns(_NURSE, "appeal-2") is False
    assert service.principal_owns(_NURSE, None) is False

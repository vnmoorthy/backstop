"""Shared fixtures for the controller HTTP/WS tests.

Builds a ``TestClient`` over the real ``create_app()`` (so the lifespan wires a
genuine sim container) and mints valid HS256 bearer tokens matching the default
``Settings`` auth secret/issuer, so the auth gate is exercised end-to-end rather
than stubbed.
"""

from __future__ import annotations

import datetime as _dt
import os
from typing import Iterator, List, Optional

import jwt
import pytest
from fastapi.testclient import TestClient

import backstop.infra.config as config_module
from backstop.app import create_app
from backstop.infra.config import Settings

# Use a >=32-byte auth secret so PyJWT does not emit an InsecureKeyLengthWarning
# (the suite runs with ``filterwarnings = ["error"]``). Set before the app loads
# settings and clear the cached singleton so the app and the token minter agree.
os.environ["BACKSTOP_AUTH_SECRET"] = "test-secret-key-at-least-32-bytes-long-0123456789"
config_module.load_settings.cache_clear()

# Settings the app under test will load (now carrying the strong secret).
_SETTINGS = Settings()
SECRET = _SETTINGS.backstop_auth_secret
ISSUER = _SETTINGS.backstop_auth_issuer
ALLOWED_ORIGIN = _SETTINGS.cors_allow_origins[0]


def make_token(
    *,
    subject: str = "agent-1",
    role: str = "agent",
    owned: Optional[List[str]] = None,
    expires_in: int = 3600,
    issuer: str = ISSUER,
    secret: str = SECRET,
) -> str:
    """Mint a valid HS256 bearer token for the given principal."""
    now = _dt.datetime.now(tz=_dt.timezone.utc)
    claims = {
        "sub": subject,
        "role": role,
        "owned": owned if owned is not None else [],
        "iss": issuer,
        "exp": now + _dt.timedelta(seconds=expires_in),
    }
    return jwt.encode(claims, secret, algorithm="HS256")


def auth_header(**kwargs: object) -> dict:
    """Return an ``Authorization: Bearer <token>`` header dict."""
    return {"Authorization": f"Bearer {make_token(**kwargs)}"}  # type: ignore[arg-type]


@pytest.fixture()
def client() -> Iterator[TestClient]:
    """A TestClient over the wired app (lifespan runs: startup + shutdown)."""
    # Ensure the app re-reads settings carrying the strong test secret, even if a
    # prior test populated the cached singleton with the default secret.
    config_module.load_settings.cache_clear()
    with TestClient(create_app()) as test_client:
        yield test_client
    config_module.load_settings.cache_clear()

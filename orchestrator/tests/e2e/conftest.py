"""E2E fixtures: reuse the controller test's strong-secret setup + client.

Importing the controllers conftest module sets the >=32-byte auth secret and
clears the cached settings singleton as a side effect, so the booted app and the
minted tokens agree. The ``client`` fixture and token helpers are re-exported.
"""

from __future__ import annotations

from typing import Iterator

import pytest
from fastapi.testclient import TestClient

import backstop.infra.config as config_module
from backstop.app import create_app

# Side-effecting import: sets BACKSTOP_AUTH_SECRET and clears the settings cache.
from tests.controllers.conftest import auth_header, make_token  # noqa: F401


@pytest.fixture()
def client() -> Iterator[TestClient]:
    """A TestClient over the wired app (lifespan runs: startup + shutdown)."""
    config_module.load_settings.cache_clear()
    with TestClient(create_app()) as test_client:
        yield test_client
    config_module.load_settings.cache_clear()

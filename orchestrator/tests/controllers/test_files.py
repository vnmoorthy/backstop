"""Files controller: serving requires a valid signed URL (token + expiry).

The signed URL is minted by the file store only after a per-appeal ownership
check; the controller redeems that capability. These tests put an artifact,
mint a URL for an owner, and assert the GET succeeds — while an unsigned,
forged, or non-owner request is refused.

Async store calls are driven through the TestClient's own event-loop portal
(``client.portal.call``) rather than spawning a fresh loop, so the running app's
loop stays the single one in play.
"""

from __future__ import annotations

from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient

from backstop.domain.errors import Forbidden
from backstop.ports.auth_port import Principal
from backstop.ports.file_store_port import ArtifactScope


async def _put(store: object, appeal_id: str) -> object:
    """Store a fake PDF under ``appeal_id`` and return its ref."""
    return await store.put(  # type: ignore[attr-defined]
        b"%PDF-1.4 fake letter",
        scope=ArtifactScope(appeal_id=appeal_id, kind="appeal_letter"),
        ttl_seconds=300,
    )


async def _sign(store: object, ref: object, owner: Principal) -> str:
    """Mint and return the signed URL path for ``ref`` and ``owner``."""
    signed = await store.get_signed_url(ref, principal=owner)  # type: ignore[attr-defined]
    return str(signed.url)


def _put_and_sign(client: TestClient, appeal_id: str, owner: Principal) -> str:
    """Store a PDF and mint a signed URL via the TestClient's loop portal."""
    store = client.app.state.container.files
    ref = client.portal.call(_put, store, appeal_id)
    return client.portal.call(_sign, store, ref, owner)


def test_files_requires_a_token(client: TestClient) -> None:
    """A bare ``/files/{ref}`` with no signed token is rejected."""
    resp = client.get("/files/some-ref")
    # Missing required query params -> 422 from validation (never serves bytes).
    assert resp.status_code == 422


def test_files_served_with_valid_signed_url(client: TestClient) -> None:
    """An owner's signed URL serves the artifact bytes."""
    owner = Principal(subject="agent-1", role="agent", owned_ids=frozenset({"ap-1"}))
    url = _put_and_sign(client, "ap-1", owner)

    # The signed URL is root-relative (``/files/{ref}?expires=...&token=...``).
    resp = client.get(url)
    assert resp.status_code == 200
    assert resp.content.startswith(b"%PDF")


def test_files_rejects_forged_token(client: TestClient) -> None:
    """A tampered token does not serve the artifact (403)."""
    owner = Principal(subject="agent-1", role="agent", owned_ids=frozenset({"ap-1"}))
    url = _put_and_sign(client, "ap-1", owner)

    split = urlsplit(url)
    params = parse_qs(split.query)
    ref = split.path.rsplit("/", 1)[-1]
    expires = params["expires"][0]
    forged = client.get(f"/files/{ref}", params={"expires": expires, "token": "deadbeef"})
    assert forged.status_code == 403


def test_signed_url_denied_to_non_owner(client: TestClient) -> None:
    """The store refuses to mint a signed URL for a non-owning principal."""
    store = client.app.state.container.files
    ref = client.portal.call(_put, store, "ap-9")
    stranger = Principal(subject="x", role="agent", owned_ids=frozenset())
    with pytest.raises(Forbidden):
        client.portal.call(_sign, store, ref, stranger)

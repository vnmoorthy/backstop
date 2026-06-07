"""FastAPI application entrypoint + lifespan.

Builds the immutable :class:`Container` exactly once, stores it on
``app.state.container``, installs the security + logging middleware, mounts every
controller router (the JSON API under ``/v1``, the signed-file and health probes
at the root, the WebSocket stream under ``/v1``), and serves the static
dashboard at ``/``.

The lifespan owns graceful startup/shutdown: on startup it connects durable
persistence and reconciles the concurrency gate; on shutdown it drains tracked
tasks and closes the HTTP clients, gate, event bus, and DB (closes audit
findings #10 fire-and-forget and #11 graceful shutdown).

``app = create_app()`` is exposed so ``uvicorn backstop.app:app`` boots the v2
application directly.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from backstop.composition.container import Container
from backstop.composition.wiring import build_container
from backstop.controllers import (
    appeals_controller,
    files_controller,
    health_controller,
    ingestion_controller,
    review_controller,
    triage_controller,
    ws_controller,
)
from backstop.infra.config import load_settings
from backstop.infra.logging import configure_logging
from backstop.infra.security_headers import install_security_middleware

# The redesigned dashboard lives at the repository root: app.py is at
# orchestrator/backstop/app.py, so parents[2] is the repo root, then ``web``.
_WEB_DIR = Path(__file__).resolve().parents[2] / "web"

# Drain budget for tracked background tasks on shutdown (seconds).
_DRAIN_TIMEOUT_S = 5.0


async def _startup(container: Container) -> None:
    """Connect durable persistence and reconcile the gate (best-effort)."""
    repo = container.repo
    connect = getattr(repo, "connect", None)
    if connect is not None:
        await connect()
    gate = container.gate
    if gate is not None:
        await gate.reconcile()


async def _shutdown(container: Container) -> None:
    """Drain tracked tasks and close every owned resource (idempotent)."""
    tasks = container.tasks
    if tasks is not None:
        await tasks.drain(_DRAIN_TIMEOUT_S)

    for client in container.http_clients:
        aclose = getattr(client, "aclose", None)
        if aclose is not None:
            await aclose()

    events = container.events
    if events is not None:
        await events.close()

    gate = container.gate
    if gate is not None:
        await gate.aclose()

    repo = container.repo
    repo_aclose = getattr(repo, "aclose", None)
    if repo_aclose is not None:
        await repo_aclose()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Manage startup/shutdown around the wired container."""
    settings = load_settings()
    configure_logging(settings)
    container = build_container(settings)
    app.state.container = container
    try:
        await _startup(container)
        yield
    finally:
        await _shutdown(container)


def create_app() -> FastAPI:
    """Construct and configure the FastAPI application.

    Installs the lifespan, security + logging middleware, controller routers
    (API under ``/v1``; signed files, health and the dashboard at the root), and
    mounts the static dashboard last so API routes win.

    Returns:
        The configured :class:`fastapi.FastAPI` instance.
    """
    settings = load_settings()
    app = FastAPI(
        title="Backstop",
        description="Voice-agent swarm that recovers denied insurance claims.",
        lifespan=lifespan,
    )
    install_security_middleware(app, settings)

    # JSON API + WebSocket stream under /v1.
    app.include_router(appeals_controller.router, prefix="/v1")
    app.include_router(ingestion_controller.router, prefix="/v1")
    app.include_router(review_controller.router, prefix="/v1")
    app.include_router(triage_controller.router, prefix="/v1")
    app.include_router(ws_controller.router, prefix="/v1")

    # Signed-file serving + health probes at the root (the file URL is minted as
    # ``/files/{ref}``; health probes are unauthenticated by contract).
    app.include_router(files_controller.router)
    app.include_router(health_controller.router)

    # Static dashboard last so the API routes above win on overlap.
    if _WEB_DIR.is_dir():
        app.mount("/", StaticFiles(directory=str(_WEB_DIR), html=True), name="web")

    return app


app = create_app()

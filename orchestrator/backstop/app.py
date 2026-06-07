"""FastAPI application entrypoint + lifespan (skeleton).

Builds the immutable :class:`Container` exactly once, stores it on
``app.state.container``, mounts security middleware and controllers, and manages
a graceful lifespan: on startup it connects the DB and calls
``ConcurrencyGatePort.reconcile()``; on shutdown it drains tracked tasks and
closes clients (closes audit findings #10 fire-and-forget and #11 graceful
shutdown).

Controllers construct nothing — they read the container from ``app.state``.
The wiring/controller bodies are filled in by WS-Integration; this skeleton
imports cleanly and type-checks.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI

from backstop.composition.wiring import build_container
from backstop.infra.config import load_settings


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Manage startup/shutdown around the wired container.

    Startup: connect the DB and run ``gate.reconcile()``. Shutdown: drain the
    task supervisor and ``aclose()`` the gate / HTTP client / DB. The concrete
    startup and drain calls are filled in by WS-Integration.

    Args:
        app: The FastAPI application whose ``state`` carries the container.

    Yields:
        Control to the running application between startup and shutdown.
    """
    settings = load_settings()
    container = build_container(settings)
    app.state.container = container
    try:
        # WS-Integration: await container.gate.reconcile(); db.connect(); etc.
        yield
    finally:
        # WS-Integration: await container.tasks.drain(...); gate.aclose(); etc.
        pass


def create_app() -> FastAPI:
    """Construct and configure the FastAPI application.

    Installs the lifespan, security middleware, and controller routers. The
    middleware/router mounting is filled in by WS-Integration; today this
    returns a minimally-configured app that imports and starts cleanly.

    Returns:
        The configured :class:`fastapi.FastAPI` instance.
    """
    app = FastAPI(
        title="Backstop",
        description="Voice-agent swarm that recovers denied insurance claims.",
        lifespan=lifespan,
    )
    # WS-Integration: install_security_middleware(app, settings); mount routers.
    return app


app = create_app()

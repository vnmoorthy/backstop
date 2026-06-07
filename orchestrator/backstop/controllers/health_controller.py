"""Health controller: split liveness vs readiness probes (unauthenticated).

``GET /healthz`` is liveness — the process is up and serving; it never touches a
dependency, so it stays cheap and is always ``200`` once the app is running.
``GET /readyz`` is readiness — the wired container is present and its critical
ports resolved; it returns ``503`` until the lifespan has populated the
container. Both probes are intentionally unauthenticated (closes the
healthz-split finding); they expose no PHI and no secret material.
"""

from __future__ import annotations

from typing import Dict

from fastapi import APIRouter, Request, Response, status

router = APIRouter(tags=["health"])


@router.get("/healthz")
async def healthz() -> Dict[str, str]:
    """Liveness probe: the process is up (no dependency touched)."""
    return {"status": "ok"}


@router.get("/readyz")
async def readyz(request: Request, response: Response) -> Dict[str, object]:
    """Readiness probe: the wired container and its critical ports resolved."""
    container = getattr(request.app.state, "container", None)
    critical = {
        "container": container is not None,
        "repo": container is not None and container.repo is not None,
        "auth": container is not None and container.auth is not None,
        "routing": container is not None and container.routing is not None,
        "gateway": container is not None and container.gateway is not None,
    }
    ready = all(critical.values())
    if not ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return {"ready": ready, "checks": critical}

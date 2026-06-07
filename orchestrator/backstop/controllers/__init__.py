"""L5 controllers — the FastAPI HTTP/WebSocket edge (no business logic).

Controllers translate HTTP/WS requests into service calls and DTOs into wire
schemas. They construct nothing: every collaborator is read from the immutable
:class:`~backstop.composition.container.Container` on ``app.state`` via the
dependency helpers in :mod:`backstop.controllers.dependencies`. Every route
except the health probes is authenticated through the :class:`AuthPort` and
returns redacted-only response bodies.
"""

from __future__ import annotations

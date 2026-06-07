"""Triage controller: the recoverable-$ x SOL-urgency worklist.

``GET /triage`` returns appeals ranked most-urgent first by the pure triage
score. Authn-gated; the response carries only redacted appeal views plus the
deterministic score (no PHI).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, Query

from backstop.controllers.appeals_controller import appeal_to_out
from backstop.controllers.dependencies import (
    get_container,
    get_principal,
    require_worklist_reader,
)
from backstop.controllers.schemas import TriageItemOut, TriageListOut
from backstop.ports.auth_port import Principal

if TYPE_CHECKING:  # pragma: no cover - typing only
    from backstop.composition.container import Container

router = APIRouter(prefix="/triage", tags=["triage"])


@router.get("", response_model=TriageListOut)
async def triage_worklist(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    principal: Principal = Depends(get_principal),
    container: Container = Depends(get_container),
) -> TriageListOut:
    """Return the urgency-ranked appeal worklist (authn + worklist-reader role)."""
    require_worklist_reader(principal)
    service = container.triage_service
    assert service is not None  # noqa: S101
    items = await service.worklist(limit=limit, offset=offset)
    return TriageListOut(
        items=[
            TriageItemOut(appeal=appeal_to_out(item.appeal), score=item.score.value)
            for item in items
        ]
    )

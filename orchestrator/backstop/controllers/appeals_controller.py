"""Appeals controller: open an appeal and read its redacted view.

``POST /appeals`` opens an appeal from a synthetic denial (PHI arrives
pre-hashed) and returns the redacted aggregate view. ``GET /appeals/{id}``
returns the same redacted view but is gated by per-appeal ownership through the
:class:`AuthPort` (an agent/nurse may only read appeals they own; admins are
exempt). Every route is authenticated by the :func:`get_principal` dependency.
"""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, HTTPException, status

from backstop.controllers.dependencies import (
    get_auth_service,
    get_container,
    get_principal,
    require_authorized,
)
from backstop.controllers.schemas import AppealOut, CreateAppealRequest
from backstop.domain.errors import AppealNotFound
from backstop.domain.models import Denial, Payer
from backstop.domain.money import Money, RecoverableDollars, SolDeadline
from backstop.ports.auth_port import Principal

if TYPE_CHECKING:  # pragma: no cover - typing only
    from backstop.composition.container import Container
    from backstop.domain.models import Appeal
    from backstop.services.auth_service import AuthService

router = APIRouter(prefix="/appeals", tags=["appeals"])


def appeal_to_out(appeal: Appeal) -> AppealOut:
    """Map an :class:`Appeal` aggregate to its redacted-only wire view."""
    return AppealOut(
        id=appeal.id,
        status=appeal.status.value,
        route=appeal.route.value if appeal.route is not None else None,
        payer_id=appeal.denial.payer.payer_id,
        denial_code=appeal.denial.denial_code,
        recoverable_cents=appeal.recoverable.amount.cents,
        sol_deadline=appeal.sol.deadline.isoformat(),
        needs_human_review=appeal.needs_human_review,
    )


@router.post("", response_model=AppealOut, status_code=status.HTTP_201_CREATED)
async def create_appeal(
    body: CreateAppealRequest,
    principal: Principal = Depends(get_principal),  # noqa: B008
    auth: AuthService = Depends(get_auth_service),  # noqa: B008
    container: Container = Depends(get_container),  # noqa: B008
) -> AppealOut:
    """Open an appeal from a denial (authn + RBAC ``create`` gate)."""
    require_authorized(
        auth, principal, action="create", resource="appeals", resource_id="*"
    )
    service = container.appeal_service
    assert service is not None  # noqa: S101 - container fully wired by lifespan

    denial = _denial_from_request(body)
    result = await service.create(
        denial,
        recoverable=RecoverableDollars(Money(cents=body.recoverable_cents)),
        sol=SolDeadline.from_iso(body.sol_deadline),
        needs_human_review=body.needs_human_review,
    )
    return appeal_to_out(result.appeal)


@router.get("/{appeal_id}", response_model=AppealOut)
async def get_appeal(
    appeal_id: str,
    principal: Principal = Depends(get_principal),  # noqa: B008
    auth: AuthService = Depends(get_auth_service),  # noqa: B008
    container: Container = Depends(get_container),  # noqa: B008
) -> AppealOut:
    """Read one appeal's redacted view (authn + per-appeal ownership)."""
    require_authorized(
        auth, principal, action="read", resource="appeals", resource_id=appeal_id
    )
    service = container.appeal_service
    assert service is not None  # noqa: S101
    try:
        appeal = await service.get(appeal_id)
    except AppealNotFound as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="appeal not found"
        ) from exc
    return appeal_to_out(appeal)


def _denial_from_request(body: CreateAppealRequest) -> Denial:
    """Build a domain :class:`Denial` from the validated request DTO."""
    d = body.denial
    try:
        dos = date.fromisoformat(d.dos)
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail="dos must be an ISO-8601 date",
        ) from exc
    return Denial(
        denial_id=f"denial-{d.claim_number_hash[:12]}",
        payer=Payer(payer_id=d.payer_id, name=d.payer_name),
        plan=d.plan,
        member_id_hash=d.member_id_hash,
        claim_number_hash=d.claim_number_hash,
        denial_code=d.denial_code,
        rarc=d.rarc,
        cpt=tuple(d.cpt),
        billed=Money(cents=d.billed_cents),
        dos=dos,
        npis=tuple(d.npis),
    )

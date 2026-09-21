"""ARCH-13 Step 13.8 — the review queue API.

    GET  /workspaces/{id}/verifications?status=DISAGREED
    GET  /workspaces/{id}/verifications/{verification_id}
    POST /workspaces/{id}/verifications/{verification_id}/resolve

`RequireWorkspaceContributor` throughout. Reviewing an extraction is editing
document data, which is contributor work; a viewer may not change what an
invoice says. Cross-tenant reads return 404 (ARCH-14 §14.7).
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.api import deps
from app.models.verification import (
    DocumentVerification,
    VerificationStatus,
)
from app.schemas.verification import (
    VerificationDetailResponse,
    VerificationResolveRequest,
    VerificationSummaryResponse,
)
from app.services import document_verification_service as dv

router = APIRouter(tags=["Verifications"])
logger = logging.getLogger("app.api.v1.verifications")


def _get_scoped(
    db: Session, *, verification_id: uuid.UUID, workspace_id: uuid.UUID
) -> DocumentVerification:
    verification = db.execute(
        select(DocumentVerification)
        .options(selectinload(DocumentVerification.fields))
        .where(
            DocumentVerification.id == verification_id,
            DocumentVerification.workspace_id == workspace_id,
        )
    ).scalar_one_or_none()

    if verification is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Verification not found.",
        )
    return verification


@router.get(
    "",
    response_model=list[VerificationSummaryResponse],
    summary="List document verifications",
    response_description="Verifications for this workspace, newest first.",
)
async def list_verifications(
    db: Session = Depends(deps.get_db),
    context: deps.TenantContext = Depends(deps.RequireWorkspaceContributor),
    verification_status: Optional[VerificationStatus] = Query(
        None,
        alias="status",
        description="Filter by status. DISAGREED is the review queue.",
    ),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
) -> Any:
    stmt = (
        select(DocumentVerification)
        .where(DocumentVerification.workspace_id == context.workspace_id)
        .order_by(DocumentVerification.created_at.desc())
        .offset(skip)
        .limit(limit)
    )
    if verification_status is not None:
        stmt = stmt.where(DocumentVerification.status == verification_status)

    return db.execute(stmt).scalars().all()


@router.get(
    "/{verification_id}",
    response_model=VerificationDetailResponse,
    summary="Get one verification with its per-field breakdown",
)
async def get_verification(
    verification_id: uuid.UUID,
    db: Session = Depends(deps.get_db),
    context: deps.TenantContext = Depends(deps.RequireWorkspaceContributor),
) -> Any:
    return _get_scoped(
        db, verification_id=verification_id, workspace_id=context.workspace_id
    )


@router.post(
    "/{verification_id}/resolve",
    response_model=VerificationDetailResponse,
    summary="Resolve a disagreed verification",
    response_description=(
        "The resolved verification. Emitting work_item.verification_completed "
        "is what releases the blocked automation."
    ),
)
async def resolve_verification(
    verification_id: uuid.UUID,
    body: VerificationResolveRequest,
    db: Session = Depends(deps.get_db),
    context: deps.TenantContext = Depends(deps.RequireWorkspaceContributor),
) -> Any:
    verification = _get_scoped(
        db, verification_id=verification_id, workspace_id=context.workspace_id
    )

    # ARCH40-S1:verification-resolve-delegates. The state change, the audit
    # row and `trigger.review.cleared` all come from the shared resolution
    # service, which the review hub also calls. Before ARCH-40 this endpoint
    # wrote no audit row at all, so "who cleared this extraction" had no
    # answer; a hub that wrote its own row would have produced a different
    # answer depending on which screen the reviewer used.
    #
    # `_requeue_automation` below is still called and is still the thing
    # verify gates read. The shared service requeues with the same
    # `automation:execute:{event.id}` idempotency key, and `job_service.enqueue`
    # returns the existing job for a key it has already seen, so the two
    # cannot produce two walks.
    from app.services.review import resolution as review_resolution

    try:
        review_resolution.resolve_scoped(
            db,
            workspace_id=context.workspace_id,
            kind="EXTRACTION",
            item_id=verification.id,
            actor_user_id=context.user_id,
            payload=review_resolution.ResolvePayload(values=dict(body.values)),
        )
        _requeue_automation(db, verification=verification)
        db.commit()
    except LookupError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc
    except review_resolution.ReviewResolutionError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc

    logger.info(
        "verification.resolved_via_api",
        extra={
            "verification_id": str(verification.id),
            "workspace_id": str(context.workspace_id),
            "user_id": str(context.user_id),
        },
    )
    db.refresh(verification)
    return verification


def _requeue_automation(db: Session, *, verification: DocumentVerification) -> None:
    from app.models.outbox_event import OutboxEvent
    from app.services import job_service

    event = db.execute(
        select(OutboxEvent)
        .where(
            OutboxEvent.resource_id == verification.work_item_id,
            OutboxEvent.event_type == "work_item.verification_completed",
        )
        .order_by(OutboxEvent.seq.desc())
        .limit(1)
    ).scalar_one_or_none()
    if event is None:
        return

    job_service.enqueue(
        db,
        job_type="automation.execute",
        organization_id=verification.organization_id,
        payload={"outbox_event_id": str(event.id)},
        idempotency_key=f"automation:execute:{event.id}",
    )


__all__ = ["router"]

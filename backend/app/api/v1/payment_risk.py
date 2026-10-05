"""PHASE 4 — payment-risk flags (radar): list, confirm, dismiss.

    GET  /workspaces/{wid}/payment-risk                       [VIEWER]
    POST /workspaces/{wid}/payment-risk/{flag_id}/confirm     [CONTRIBUTOR]
    POST /workspaces/{wid}/payment-risk/{flag_id}/dismiss     [CONTRIBUTOR, reason >= 10 chars]

Same plan capability as the radar (capability.anomaly_radar) and the same
review rule as an anomaly: a dismissal needs a reason the next reviewer can
read instead of re-deciding. Every decision is audited (REVIEW_ITEM).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api import capability_gate
from app.api.deps import RequireWorkspaceRole, TenantContext, get_db
from app.core import entitlements
from app.models.audit_log import AuditAction, AuditOutcome, AuditResourceType
from app.models.payment_risk import STATUS_CONFIRMED, STATUS_DISMISSED, STATUS_OPEN, PaymentRiskFlag
from app.models.work_item import WorkItem
from app.models.workspace import WorkspaceRole
from app.services import audit_service

router = APIRouter(tags=["Forensic Audit Radar"])

RequireViewer = RequireWorkspaceRole(WorkspaceRole.VIEWER)
RequireContributor = RequireWorkspaceRole(WorkspaceRole.CONTRIBUTOR)
MIN_DISMISS_REASON = 10


class PaymentRiskFlagOut(BaseModel):
    id: uuid.UUID
    work_item_id: uuid.UUID
    document: Optional[str] = None
    counterpart_work_item_id: Optional[uuid.UUID] = None
    kind: str
    severity: str
    status: str
    summary: str
    details: dict[str, Any]
    review_note: Optional[str] = None
    reviewed_at: Optional[datetime] = None
    created_at: datetime


class PaymentRiskList(BaseModel):
    items: list[PaymentRiskFlagOut]
    counts_by_status: dict[str, int]


class DismissRequest(BaseModel):
    reason: str = Field(min_length=MIN_DISMISS_REASON, max_length=2000)


def _scope(db: Session, context: TenantContext, workspace_id: uuid.UUID, operation: str) -> None:
    if context.workspace_id != workspace_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Workspace not found.")
    capability_gate.require_capability(
        db, context=context, capability_key=entitlements.ANOMALY_RADAR_CAPABILITY, operation=operation
    )


def _out(flag: PaymentRiskFlag, document: Optional[str]) -> PaymentRiskFlagOut:
    return PaymentRiskFlagOut(
        id=flag.id, work_item_id=flag.work_item_id, document=document,
        counterpart_work_item_id=flag.counterpart_work_item_id, kind=flag.kind, severity=flag.severity,
        status=flag.status, summary=flag.summary, details=dict(flag.details or {}),
        review_note=flag.review_note, reviewed_at=flag.reviewed_at, created_at=flag.created_at,
    )


def _load(db: Session, workspace_id: uuid.UUID, flag_id: uuid.UUID) -> PaymentRiskFlag:
    flag = db.execute(
        select(PaymentRiskFlag).where(PaymentRiskFlag.id == flag_id, PaymentRiskFlag.workspace_id == workspace_id)
    ).scalar_one_or_none()
    if flag is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Flag not found.")
    return flag


@router.get("/workspaces/{workspace_id}/payment-risk", response_model=PaymentRiskList,
            summary="Bank-account changes and round totals on invoices")
def list_payment_risk(
    workspace_id: uuid.UUID,
    status_filter: Optional[Literal["OPEN", "CONFIRMED", "DISMISSED"]] = Query(None, alias="status"),
    db: Session = Depends(get_db),
    context: TenantContext = Depends(RequireViewer),
) -> PaymentRiskList:
    _scope(db, context, workspace_id, "payment_risk.list")
    stmt = (
        select(PaymentRiskFlag, WorkItem.original_filename)
        .join(WorkItem, WorkItem.id == PaymentRiskFlag.work_item_id)
        .where(PaymentRiskFlag.workspace_id == workspace_id)
        .order_by(PaymentRiskFlag.created_at.desc())
        .limit(200)
    )
    if status_filter:
        stmt = stmt.where(PaymentRiskFlag.status == status_filter)
    counts = dict(db.execute(
        select(PaymentRiskFlag.status, func.count()).where(PaymentRiskFlag.workspace_id == workspace_id)
        .group_by(PaymentRiskFlag.status)
    ).all())
    return PaymentRiskList(items=[_out(flag, name) for flag, name in db.execute(stmt).all()],
                           counts_by_status={k: int(v) for k, v in counts.items()})


def _decide(db: Session, context: TenantContext, workspace_id: uuid.UUID, flag_id: uuid.UUID,
            decision: str, note: Optional[str]) -> PaymentRiskFlagOut:
    flag = _load(db, workspace_id, flag_id)
    if flag.status != STATUS_OPEN:
        raise HTTPException(status.HTTP_409_CONFLICT, f"This flag was already {flag.status.lower()}.")
    flag.status, flag.review_note = decision, note
    flag.reviewed_by_user_id, flag.reviewed_at = context.user_id, datetime.now(timezone.utc)
    audit_service.record(
        db, organization_id=context.organization_id, workspace_id=workspace_id, actor_id=context.user_id,
        resource_type=AuditResourceType.REVIEW_ITEM, resource_id=flag.id, action=AuditAction.UPDATED,
        outcome=AuditOutcome.ALLOWED,
        details={"review_kind": "PAYMENT_RISK", "review_item_id": str(flag.id), "kind": flag.kind,
                 "work_item_id": str(flag.work_item_id), "severity": flag.severity, "resolution": decision},
    )
    db.commit()
    db.refresh(flag)
    document = db.execute(select(WorkItem.original_filename).where(WorkItem.id == flag.work_item_id)).scalar()
    return _out(flag, document)


@router.post("/workspaces/{workspace_id}/payment-risk/{flag_id}/confirm", response_model=PaymentRiskFlagOut)
def confirm_payment_risk(
    workspace_id: uuid.UUID,
    flag_id: uuid.UUID,
    db: Session = Depends(get_db),
    context: TenantContext = Depends(RequireContributor),
) -> PaymentRiskFlagOut:
    _scope(db, context, workspace_id, "payment_risk.confirm")
    return _decide(db, context, workspace_id, flag_id, STATUS_CONFIRMED, None)


@router.post("/workspaces/{workspace_id}/payment-risk/{flag_id}/dismiss", response_model=PaymentRiskFlagOut)
def dismiss_payment_risk(
    workspace_id: uuid.UUID,
    flag_id: uuid.UUID,
    body: DismissRequest,
    db: Session = Depends(get_db),
    context: TenantContext = Depends(RequireContributor),
) -> PaymentRiskFlagOut:
    _scope(db, context, workspace_id, "payment_risk.dismiss")
    return _decide(db, context, workspace_id, flag_id, STATUS_DISMISSED, body.reason.strip())

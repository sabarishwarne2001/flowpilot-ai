"""ARCH-31 Step 3 — procurement matching endpoints.

    GET  /workspaces/{wid}/procurement/cases                  queue    [VIEWER]
    GET  /workspaces/{wid}/procurement/cases/{cid}            detail   [VIEWER]
    POST /workspaces/{wid}/procurement/cases/{cid}/approve    approve  [CONTRIBUTOR]
    POST /workspaces/{wid}/procurement/cases/{cid}/dispute    dispute  [CONTRIBUTOR]
    POST /workspaces/{wid}/procurement/cases/{cid}/rematch    rematch  [CONTRIBUTOR]
    GET  /workspaces/{wid}/procurement/policies               list     [VIEWER]
    POST /workspaces/{wid}/procurement/policies               publish  [ADMIN]
    POST /workspaces/{wid}/procurement/policies/preview       preview  [ADMIN]
    PUT  /workspaces/{wid}/procurement/roles/{work_item_id}   override [CONTRIBUTOR]

WHY APPROVE IS CONTRIBUTOR AND PUBLISH IS ADMIN
===============================================

Approving a case authorises one invoice. That is the daily work of an accounts
payable clerk, and putting it behind ADMIN means the person who does the job
cannot do the job.

Publishing a tolerance policy changes what counts as an exception for every
future case in the workspace, permanently — the row is immutable once
published. A setting that silently widens what gets waved through is an
administrative decision, and it is the setting an attacker with a clerk's
credentials would reach for first.

EVERY ROUTE IS CAPABILITY-GATED, INCLUDING THE READS
====================================================

`require_capability` runs on the queue and the detail view as well as the
writes. Gating only the writes would let a tenant without the capability read
every variance the engine found and simply not click approve, which is the
product.

The gate raises `CapabilityRequiredError` -> 402 with the ARCH-01 envelope and
`remedy: PLAN_UPGRADE`. It is not caught here: `app/core/exception_handlers.py`
renders it, so the shape is identical to every other domain refusal and the
console's `ApiError` handling needs no new branch.

WHY REMATCH ENQUEUES RATHER THAN SCORING INLINE
===============================================

Scoring is fast, but it is not a request-cycle operation on a document with
two hundred lines, and more importantly it is the SAME work the sweep does.
Running it inline here would be a second code path to the same outcome, and
the two would drift. The route enqueues `procurement.score` with the forced
counterparts and returns 202 with the job id.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api import capability_gate
from app.api.deps import RequireWorkspaceRole, TenantContext, get_db
from app.core import entitlements
from app.models.document_role import DocumentRole
from app.models.workspace import WorkspaceRole
from app.models.procurement import ProcurementTolerancePolicy
from app.schemas.procurement import (
    CaseApproveRequest,
    CaseDetailResponse,
    CaseDisputeRequest,
    CaseRematchRequest,
    CaseSummaryResponse,
    DocumentRoleOverrideRequest,
    ImpactPreviewRequest,
    ImpactPreviewResponse,
    TolerancePolicyPublishRequest,
    TolerancePolicyResponse,
)
from app.services import job_service
from app.services.procurement_matching import case_service
from app.services.procurement_matching.role_classifier import (
    SOURCE_USER,
    UserRoleLocked,
)

logger = logging.getLogger("app.api.v1.procurement")

router = APIRouter(tags=["Procurement Matching"])

RequireViewer = RequireWorkspaceRole(WorkspaceRole.VIEWER)
RequireContributor = RequireWorkspaceRole(WorkspaceRole.CONTRIBUTOR)
RequireAdmin = RequireWorkspaceRole(WorkspaceRole.ADMIN)

CAPABILITY = entitlements.RECONCILIATION_CAPABILITY


def _gate(db: Session, context: TenantContext, operation: str) -> None:
    capability_gate.require_capability(
        db, context=context, capability_key=CAPABILITY, operation=operation
    )


def _assert_workspace(context: TenantContext, workspace_id: uuid.UUID) -> None:
    """The path id must be the resolved context's workspace.

    ARCH-02. `get_workspace_context` already resolved membership for the
    workspace in the path, so a mismatch here means the dependency and the
    route disagree about which workspace this is — which is a bug, not a
    permission decision, and it must never resolve in the caller's favour.
    """
    if context.workspace_id != workspace_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Workspace not found."
        )


# ---------------------------------------------------------------------------
# Cases
# ---------------------------------------------------------------------------

_VENDOR_NAME_KEYS = ("vendor_name", "supplier_name", "seller_name", "vendor", "supplier", "seller")


def _vendor_name(entities: Any) -> Optional[str]:
    if not isinstance(entities, dict):
        return None
    for key in _VENDOR_NAME_KEYS:
        value = entities.get(key)
        if isinstance(value, dict):
            value = value.get("value")
        if isinstance(value, str) and value.strip():
            return value.strip()[:300]
    return None


def _annotated(db: Session, workspace_id: uuid.UUID, cases: list[Any], schema: Any) -> list[Any]:
    """F-172 / F-174: each case with the currency, vendor name and document numbers a person reads.

    Two indexed reads for the whole page (roles and documents of every side), never one per case.
    """
    from app.models.work_item import WorkItem

    ids = {
        work_item_id
        for case in cases
        for work_item_id in (case.invoice_work_item_id, case.po_work_item_id, case.receipt_work_item_id)
        if work_item_id is not None
    }
    roles: dict[uuid.UUID, DocumentRole] = {}
    documents: dict[uuid.UUID, Any] = {}
    if ids:
        roles = {
            role.work_item_id: role
            for role in db.execute(
                select(DocumentRole).where(
                    DocumentRole.workspace_id == workspace_id, DocumentRole.work_item_id.in_(ids)
                )
            ).scalars()
        }
        documents = {
            row.id: row
            for row in db.execute(
                select(WorkItem.id, WorkItem.original_filename, WorkItem.extracted_entities).where(
                    WorkItem.workspace_id == workspace_id, WorkItem.id.in_(ids)
                )
            )
        }
    out = []
    for case in cases:
        invoice, po, receipt = (
            roles.get(case.invoice_work_item_id), roles.get(case.po_work_item_id), roles.get(case.receipt_work_item_id)
        )
        sides = [d for d in (documents.get(case.invoice_work_item_id), documents.get(case.po_work_item_id),
                             documents.get(case.receipt_work_item_id)) if d is not None]
        item = schema.model_validate(case, from_attributes=True)
        extra = {
            "currency": (invoice.currency if invoice else None) or (po.currency if po else None),
            "vendor_name": next((name for name in (_vendor_name(d.extracted_entities) for d in sides) if name), None),
            "invoice_number": invoice.document_number if invoice else None,
            "po_number": po.document_number if po else None,
            "receipt_number": receipt.document_number if receipt else None,
            "invoice_filename": getattr(documents.get(case.invoice_work_item_id), "original_filename", None),
            "po_filename": getattr(documents.get(case.po_work_item_id), "original_filename", None),
            "receipt_filename": getattr(documents.get(case.receipt_work_item_id), "original_filename", None),
            "invoice_total_micros": invoice.total_micros if invoice else None,
            "po_total_micros": po.total_micros if po else None,
        }
        out.append(item.model_copy(update=extra))
    return out



@router.get(
    "/workspaces/{workspace_id}/procurement/cases",
    response_model=list[CaseSummaryResponse],
)
def list_cases(
    workspace_id: uuid.UUID,
    db: Session = Depends(get_db),
    context: TenantContext = Depends(RequireViewer),
    case_status: Optional[list[str]] = Query(default=None, alias="status"),
    has_exceptions: Optional[bool] = Query(default=None),
    min_variance_micros: Optional[int] = Query(default=None, ge=0),
    created_after: Optional[datetime] = Query(default=None),
    created_before: Optional[datetime] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> list[Any]:
    _assert_workspace(context, workspace_id)
    _gate(db, context, "procurement.cases.list")
    cases = case_service.list_cases(
        db,
        workspace_id=workspace_id,
        statuses=case_status,
        has_exceptions=has_exceptions,
        min_variance_micros=min_variance_micros,
        created_after=created_after,
        created_before=created_before,
        limit=limit,
        offset=offset,
    )
    return _annotated(db, workspace_id, cases, CaseSummaryResponse)


@router.get(
    "/workspaces/{workspace_id}/procurement/cases/{case_id}",
    response_model=CaseDetailResponse,
)
def get_case(
    workspace_id: uuid.UUID,
    case_id: uuid.UUID,
    db: Session = Depends(get_db),
    context: TenantContext = Depends(RequireViewer),
) -> Any:
    _assert_workspace(context, workspace_id)
    _gate(db, context, "procurement.cases.read")
    case = case_service.get_case(db, workspace_id=workspace_id, case_id=case_id)
    if case is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Case not found."
        )
    return _annotated(db, workspace_id, [case], CaseDetailResponse)[0]


@router.post(
    "/workspaces/{workspace_id}/procurement/cases/{case_id}/approve",
    response_model=CaseDetailResponse,
)
def approve_case(
    workspace_id: uuid.UUID,
    case_id: uuid.UUID,
    payload: CaseApproveRequest,
    db: Session = Depends(get_db),
    context: TenantContext = Depends(RequireContributor),
) -> Any:
    _assert_workspace(context, workspace_id)
    _gate(db, context, "procurement.cases.approve")
    try:
        case = case_service.approve_case(
            db,
            workspace_id=workspace_id,
            case_id=case_id,
            actor_id=context.user_id,
            override_reason=payload.override_reason,
        )
    except case_service.OverrideReasonRequired as exc:
        # 422, not 400: the request was well-formed and the entity it
        # describes cannot be processed in its current state. The console
        # branches on this code to open the override dialog.
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "OVERRIDE_REASON_REQUIRED",
                "message": str(exc),
                "details": {"case_id": str(case_id)},
            },
        ) from exc
    except case_service.CaseNotLive as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "CASE_NOT_LIVE", "message": str(exc), "details": {}},
        ) from exc
    db.commit()
    return _annotated(
        db, workspace_id, [case_service.get_case(db, workspace_id=workspace_id, case_id=case.id)], CaseDetailResponse
    )[0]


@router.post(
    "/workspaces/{workspace_id}/procurement/cases/{case_id}/dispute",
    response_model=CaseDetailResponse,
)
def dispute_case(
    workspace_id: uuid.UUID,
    case_id: uuid.UUID,
    payload: CaseDisputeRequest,
    db: Session = Depends(get_db),
    context: TenantContext = Depends(RequireContributor),
) -> Any:
    _assert_workspace(context, workspace_id)
    _gate(db, context, "procurement.cases.dispute")
    try:
        case = case_service.dispute_case(
            db,
            workspace_id=workspace_id,
            case_id=case_id,
            actor_id=context.user_id,
            reason=payload.reason,
        )
    except case_service.CaseNotLive as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "CASE_NOT_LIVE", "message": str(exc), "details": {}},
        ) from exc
    except case_service.CaseServiceError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "DISPUTE_REASON_TOO_SHORT", "message": str(exc), "details": {}},
        ) from exc
    db.commit()
    return _annotated(
        db, workspace_id, [case_service.get_case(db, workspace_id=workspace_id, case_id=case.id)], CaseDetailResponse
    )[0]


@router.post(
    "/workspaces/{workspace_id}/procurement/cases/{case_id}/rematch",
    status_code=status.HTTP_202_ACCEPTED,
)
def rematch_case(
    workspace_id: uuid.UUID,
    case_id: uuid.UUID,
    payload: CaseRematchRequest,
    response: Response,
    db: Session = Depends(get_db),
    context: TenantContext = Depends(RequireContributor),
) -> dict[str, Any]:
    _assert_workspace(context, workspace_id)
    _gate(db, context, "procurement.cases.rematch")

    case = case_service.get_case(db, workspace_id=workspace_id, case_id=case_id)
    if case is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Case not found."
        )
    if case.invoice_work_item_id is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "CASE_HAS_NO_INVOICE",
                "message": (
                    "rematch is anchored on the invoice; this case has none."
                ),
                "details": {},
            },
        )

    with db.begin_nested():
        job = job_service.enqueue(
            db,
            job_type="procurement.score",
            organization_id=context.organization_id,
            payload={
                "workspace_id": str(workspace_id),
                "invoice_work_item_id": str(case.invoice_work_item_id),
                "po_work_item_id": (
                    str(payload.po_work_item_id) if payload.po_work_item_id else None
                ),
                "receipt_work_item_id": (
                    str(payload.receipt_work_item_id)
                    if payload.receipt_work_item_id
                    else None
                ),
                "actor_id": str(context.user_id),
                "candidate_source": "USER",
            },
        )
    db.commit()
    response.headers["Location"] = f"/api/v1/jobs/{job.id}"
    return {"job_id": str(job.id), "case_id": str(case.id), "status": "QUEUED"}


# ---------------------------------------------------------------------------
# Tolerance policies
# ---------------------------------------------------------------------------


@router.get(
    "/workspaces/{workspace_id}/procurement/policies",
    response_model=list[TolerancePolicyResponse],
)
def list_policies(
    workspace_id: uuid.UUID,
    db: Session = Depends(get_db),
    context: TenantContext = Depends(RequireViewer),
) -> list[Any]:
    _assert_workspace(context, workspace_id)
    _gate(db, context, "procurement.policies.list")
    return list(
        db.execute(
            select(ProcurementTolerancePolicy)
            .where(ProcurementTolerancePolicy.workspace_id == workspace_id)
            .order_by(ProcurementTolerancePolicy.version.desc())
            .limit(50)
        ).scalars().all()
    )


@router.post(
    "/workspaces/{workspace_id}/procurement/policies",
    response_model=TolerancePolicyResponse,
    status_code=status.HTTP_201_CREATED,
)
def publish_policy(
    workspace_id: uuid.UUID,
    payload: TolerancePolicyPublishRequest,
    db: Session = Depends(get_db),
    context: TenantContext = Depends(RequireAdmin),
) -> Any:
    _assert_workspace(context, workspace_id)
    _gate(db, context, "procurement.policies.publish")
    policy = case_service.publish_policy(
        db,
        organization_id=context.organization_id,
        workspace_id=workspace_id,
        actor_id=context.user_id,
        price_tolerance_micros=payload.price_tolerance_micros,
        price_tolerance_bps=payload.price_tolerance_bps,
        quantity_tolerance=payload.quantity_tolerance,
        max_pair_cost=payload.max_pair_cost,
        candidate_window_days=payload.candidate_window_days,
    )
    db.commit()
    db.refresh(policy)
    return policy


@router.post(
    "/workspaces/{workspace_id}/procurement/policies/preview",
    response_model=ImpactPreviewResponse,
)
def preview_policy(
    workspace_id: uuid.UUID,
    payload: ImpactPreviewRequest,
    db: Session = Depends(get_db),
    context: TenantContext = Depends(RequireAdmin),
) -> Any:
    _assert_workspace(context, workspace_id)
    _gate(db, context, "procurement.policies.preview")
    return case_service.impact_preview(
        db,
        workspace_id=workspace_id,
        price_tolerance_micros=payload.price_tolerance_micros,
        price_tolerance_bps=payload.price_tolerance_bps,
        quantity_tolerance=payload.quantity_tolerance,
        window_days=payload.window_days,
    )


# ---------------------------------------------------------------------------
# Document role override
# ---------------------------------------------------------------------------


@router.put("/workspaces/{workspace_id}/procurement/roles/{work_item_id}")
def override_document_role(
    workspace_id: uuid.UUID,
    work_item_id: uuid.UUID,
    payload: DocumentRoleOverrideRequest,
    db: Session = Depends(get_db),
    context: TenantContext = Depends(RequireContributor),
) -> dict[str, Any]:
    """Write `role_source='USER'`, which the classifier then refuses to overwrite.

    `role_confidence` is set to NULL, enforced by
    `ck_document_roles_user_has_no_confidence`. A human did not estimate
    anything, and storing 1.000 would let a later rule beat them on score.
    """
    _assert_workspace(context, workspace_id)
    _gate(db, context, "procurement.roles.override")

    row = db.execute(
        select(DocumentRole).where(
            DocumentRole.work_item_id == work_item_id,
            DocumentRole.workspace_id == workspace_id,
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="This document has no role row yet; run extraction first.",
        )

    previous = row.role
    row.role = payload.role
    row.role_source = SOURCE_USER
    row.role_confidence = None
    db.flush([row])

    # A role change invalidates whatever case this document is in: an
    # invoice reclassified as a credit note is not the document the case was
    # built from. Enqueue rather than score inline, same reasoning as
    # rematch.
    with db.begin_nested():
        job_service.enqueue(
            db,
            job_type="procurement.score",
            organization_id=context.organization_id,
            payload={"workspace_id": str(workspace_id)},
            idempotency_key=f"procurement.score:role:{work_item_id}:{payload.role}",
        )
    db.commit()

    return {
        "work_item_id": str(work_item_id),
        "role": row.role,
        "role_source": row.role_source,
        "previous_role": previous,
    }


__all__ = ["router"]
"""ARCH-34 §5.5 — the Forensic Audit Radar endpoints.

    GET  /workspaces/{wid}/anomalies                 feed      [VIEWER]
    GET  /workspaces/{wid}/anomalies/series          chart     [VIEWER]
    GET  /workspaces/{wid}/anomalies/{fid}           detail    [VIEWER]
    POST /workspaces/{wid}/anomalies/{fid}/confirm   agree     [CONTRIBUTOR]
    POST /workspaces/{wid}/anomalies/{fid}/dismiss   disagree  [CONTRIBUTOR]
    GET  /workspaces/{wid}/anomalies/suppressions    policy    [ADMIN]

WHY RESOLVING IS CONTRIBUTOR AND THE SUPPRESSION LIST IS ADMIN
==============================================================

The same split ARCH-31 and ARCH-33 made. Resolving one finding decides one
pair of documents; that is the daily work of the person in accounts payable,
and putting it behind ADMIN means the person who does the job cannot do it.

The suppression LIST is a different question — it is "what has this workspace
decided to stop being told about", which is a standing blind spot and an
administrative concern. An attacker with a reviewer's credentials reaches for
suppressions first, because a suppression is silent by design.

EVERY ROUTE IS CAPABILITY-GATED, INCLUDING THE READS
====================================================

Gating only the writes would let a tenant without the capability read every
duplicate the engine found and simply not act on them, which is the product.

The gate raises `CapabilityRequiredError` -> 402 with the ARCH-01 envelope and
`remedy: PLAN_UPGRADE`. It is not caught here; `app/core/exception_handlers.py`
renders it, so the console's existing `ApiError` handling needs no new branch.

WHY DISMISS WRITES A SUPPRESSION AND NEVER DELETES THE FINDING
==============================================================

§5.4. The finding is the record that the engine raised something and a named
person judged it; the suppression is the policy that stops the claim
resurfacing. Different records, different lifetimes. The question asked after
an invoice is paid twice is exactly "did the system see this, and what did we
do", and deleting the finding erases both halves of the answer.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api import capability_gate
from app.api.deps import RequireWorkspaceRole, TenantContext, get_db
from app.core import entitlements
from app.models.audit_log import AuditAction, AuditOutcome, AuditResourceType
from app.models.radar import AnomalyFinding
from app.models.work_item import WorkItem
from app.models.workspace import WorkspaceRole
from app.schemas.radar import (
    AnomalyConfirmRequest,
    AnomalyDismissRequest,
    AnomalyFeedCounts,
    AnomalyFeedResponse,
    AnomalyFindingDetail,
    AnomalyFindingSummary,
    AnomalySuppressionResponse,
    PriceSeriesPoint,
    PriceSeriesResponse,
)
from app.services import audit_service
from app.services.radar import candidates as candidates_module
from app.services.radar import price_surge
from app.services.radar import suppressions as suppressions_module
from app.services.radar import vocabulary as vocab

logger = logging.getLogger("app.api.v1.anomalies")

router = APIRouter(tags=["Forensic Audit Radar"])

RequireViewer = RequireWorkspaceRole(WorkspaceRole.VIEWER)
RequireContributor = RequireWorkspaceRole(WorkspaceRole.CONTRIBUTOR)
RequireAdmin = RequireWorkspaceRole(WorkspaceRole.ADMIN)

CAPABILITY = entitlements.ANOMALY_RADAR_CAPABILITY


def _gate(db: Session, context: TenantContext, operation: str) -> None:
    capability_gate.require_capability(
        db, context=context, capability_key=CAPABILITY, operation=operation
    )


def _assert_workspace(context: TenantContext, workspace_id: uuid.UUID) -> None:
    """ARCH-02. The path id must be the resolved context's workspace."""
    if context.workspace_id != workspace_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Workspace not found."
        )


def _label(item: Optional[WorkItem]) -> str:
    if item is None:
        return ""
    entities = item.extracted_entities or {}
    number = entities.get("document_number")
    if number:
        return str(number)
    return item.original_filename or ""


def _labels(db: Session, findings: list[AnomalyFinding]) -> dict[uuid.UUID, str]:
    ids: set[uuid.UUID] = set()
    for finding in findings:
        ids.add(finding.subject_work_item_id)
        if finding.counterpart_work_item_id is not None:
            ids.add(finding.counterpart_work_item_id)
    if not ids:
        return {}
    rows = db.execute(select(WorkItem).where(WorkItem.id.in_(ids))).scalars().all()
    return {row.id: _label(row) for row in rows}


def _summary(
    finding: AnomalyFinding, labels: dict[uuid.UUID, str]
) -> AnomalyFindingSummary:
    return AnomalyFindingSummary(
        id=finding.id,
        kind=finding.kind,
        layer=finding.layer,
        severity=finding.severity,
        status=finding.status,
        score=finding.score,
        headline=finding.headline,
        subject_work_item_id=finding.subject_work_item_id,
        counterpart_work_item_id=finding.counterpart_work_item_id,
        subject_label=labels.get(finding.subject_work_item_id, ""),
        counterpart_label=(
            ""
            if finding.counterpart_work_item_id is None
            else labels.get(finding.counterpart_work_item_id, "")
        ),
        created_at=finding.created_at,
        updated_at=finding.updated_at,
        resolved_at=finding.resolved_at,
        resolution_note=finding.resolution_note,
    )


def _load(
    db: Session, *, workspace_id: uuid.UUID, finding_id: uuid.UUID
) -> AnomalyFinding:
    finding = db.execute(
        select(AnomalyFinding).where(
            AnomalyFinding.id == finding_id,
            AnomalyFinding.workspace_id == workspace_id,
        )
    ).scalar_one_or_none()
    if finding is None:
        # 404 and not 403 on a workspace mismatch: confirming that a finding
        # id exists somewhere else is itself a small leak, and ARCH-02's
        # posture everywhere is that a resource outside the tenant does not
        # exist.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Finding not found."
        )
    return finding


# ===========================================================================
# Feed
# ===========================================================================


@router.get(
    "/workspaces/{workspace_id}/anomalies",
    response_model=AnomalyFeedResponse,
    summary="The audit radar feed",
)
def list_anomalies(
    workspace_id: uuid.UUID,
    kind: Optional[str] = Query(default=None),
    severity: Optional[str] = Query(default=None),
    anomaly_status: Optional[str] = Query(default=None, alias="status"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    context: TenantContext = Depends(RequireViewer),
) -> AnomalyFeedResponse:
    _assert_workspace(context, workspace_id)
    _gate(db, context, "anomalies.list")

    for value, allowed, field in (
        (kind, vocab.KINDS, "kind"),
        (severity, vocab.SEVERITIES, "severity"),
        (anomaly_status, vocab.STATUSES, "status"),
    ):
        if value is not None and value not in allowed:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"{field} must be one of {', '.join(allowed)}. "
                    f"Got {value!r}."
                ),
            )

    stmt = select(AnomalyFinding).where(AnomalyFinding.workspace_id == workspace_id)
    if kind is not None:
        stmt = stmt.where(AnomalyFinding.kind == kind)
    if severity is not None:
        stmt = stmt.where(AnomalyFinding.severity == severity)
    if anomaly_status is not None:
        stmt = stmt.where(AnomalyFinding.status == anomaly_status)

    rows = list(
        db.execute(
            stmt.order_by(AnomalyFinding.created_at.desc()).limit(limit).offset(offset)
        ).scalars()
    )
    labels = _labels(db, rows)

    # The header counts are computed over the WHOLE workspace, not the page.
    # "3 high" that means "3 high on this page" is a number that changes when
    # somebody scrolls.
    severity_counts = dict(
        db.execute(
            select(AnomalyFinding.severity, func.count())
            .where(
                AnomalyFinding.workspace_id == workspace_id,
                AnomalyFinding.status == vocab.STATUS_OPEN,
            )
            .group_by(AnomalyFinding.severity)
        ).all()
    )
    status_counts = dict(
        db.execute(
            select(AnomalyFinding.status, func.count())
            .where(AnomalyFinding.workspace_id == workspace_id)
            .group_by(AnomalyFinding.status)
        ).all()
    )

    return AnomalyFeedResponse(
        counts=AnomalyFeedCounts(
            high=int(severity_counts.get(vocab.SEVERITY_HIGH, 0)),
            medium=int(severity_counts.get(vocab.SEVERITY_MEDIUM, 0)),
            low=int(severity_counts.get(vocab.SEVERITY_LOW, 0)),
            open=int(status_counts.get(vocab.STATUS_OPEN, 0)),
            confirmed=int(status_counts.get(vocab.STATUS_CONFIRMED, 0)),
            dismissed=int(status_counts.get(vocab.STATUS_DISMISSED, 0)),
        ),
        items=[_summary(row, labels) for row in rows],
    )


@router.get(
    "/workspaces/{workspace_id}/anomalies/series",
    response_model=PriceSeriesResponse,
    summary="Unit price history for the surge chart",
)
def price_series(
    workspace_id: uuid.UUID,
    vendor_key: str = Query(min_length=1),
    sku: str = Query(min_length=1),
    db: Session = Depends(get_db),
    context: TenantContext = Depends(RequireViewer),
) -> PriceSeriesResponse:
    """The 12-month series, its median band, and the point that was flagged.

    Declared BEFORE `/{finding_id}` deliberately: FastAPI matches in
    declaration order, and `series` would otherwise be parsed as a finding id
    and 422 on the UUID coercion.
    """
    _assert_workspace(context, workspace_id)
    _gate(db, context, "anomalies.series")

    from app.models.workspace import Workspace

    workspace = db.execute(
        select(Workspace).where(Workspace.id == workspace_id)
    ).scalar_one_or_none()
    currency = getattr(workspace, "default_currency", None) or "INR"

    observations = candidates_module.series_for(
        db,
        workspace_id=workspace_id,
        vendor_key=vendor_key,
        sku=sku,
        currency=currency,
    )
    if not observations:
        return PriceSeriesResponse(
            vendor_key=vendor_key, sku=sku, currency=currency
        )

    prices = [item.unit_price_micros for item in observations]
    median = price_surge.median(prices)
    mad = price_surge.median_absolute_deviation(prices, median)
    iqr = price_surge.interquartile_range(prices)
    latest = observations[-1]
    z = price_surge.robust_z(latest.unit_price_micros, median, mad)

    return PriceSeriesResponse(
        vendor_key=vendor_key,
        sku=sku,
        currency=currency,
        points=[
            PriceSeriesPoint(
                work_item_id=uuid.UUID(item.work_item_id),
                observed_on=item.observed_on,
                unit_price_micros=item.unit_price_micros,
            )
            for item in observations
        ],
        median_micros=median,
        mad_micros=mad,
        iqr_micros=iqr,
        z=z,
        basis=(
            vocab.BASIS_ROBUST_Z if z is not None else vocab.BASIS_RELATIVE_ONLY
        ),
        flagged_work_item_id=uuid.UUID(latest.work_item_id),
    )


@router.get(
    "/workspaces/{workspace_id}/anomalies/suppressions",
    response_model=list[AnomalySuppressionResponse],
    summary="What this workspace has decided to stop being told about",
)
def list_suppressions(
    workspace_id: uuid.UUID,
    db: Session = Depends(get_db),
    context: TenantContext = Depends(RequireAdmin),
) -> list[AnomalySuppressionResponse]:
    _assert_workspace(context, workspace_id)
    _gate(db, context, "anomalies.suppressions")
    rows = suppressions_module.active_for_workspace(db, workspace_id=workspace_id)
    return [AnomalySuppressionResponse.model_validate(row) for row in rows]


@router.get(
    "/workspaces/{workspace_id}/anomalies/{finding_id}",
    response_model=AnomalyFindingDetail,
    summary="One finding, with its evidence",
)
def get_anomaly(
    workspace_id: uuid.UUID,
    finding_id: uuid.UUID,
    db: Session = Depends(get_db),
    context: TenantContext = Depends(RequireViewer),
) -> AnomalyFindingDetail:
    _assert_workspace(context, workspace_id)
    _gate(db, context, "anomalies.get")

    finding = _load(db, workspace_id=workspace_id, finding_id=finding_id)
    labels = _labels(db, [finding])
    summary = _summary(finding, labels)

    return AnomalyFindingDetail(
        **summary.model_dump(),
        metrics=dict(finding.metrics or {}),
        evidence=list(finding.evidence or []),
        engine_version=finding.engine_version,
        input_digest=finding.input_digest,
    )


# ===========================================================================
# Resolution
# ===========================================================================



def _record_review_audit(
    db: Session,
    *,
    context: TenantContext,
    workspace_id: uuid.UUID,
    finding: AnomalyFinding,
    resolution: str,
) -> None:
    """The canonical REVIEW_ITEM row, identical to the hub's.

    ARCH40-S1:anomaly-review-audit. Built from the same fields
    `review.resolution._audit` uses, in the same order, so gate B5's row
    comparison is an equality check rather than a subset check.
    """
    from app.models.audit_log import AuditResourceType as _ART
    from app.services.radar import vocabulary as _radar_vocab

    severity = str(finding.severity)
    audit_service.record(
        db,
        organization_id=context.organization_id,
        workspace_id=workspace_id,
        actor_id=context.user_id,
        resource_type=_ART.REVIEW_ITEM,
        resource_id=finding.id,
        action=AuditAction.UPDATED,
        outcome=AuditOutcome.ALLOWED,
        details={
            "review_kind": "ANOMALY",
            "review_item_id": str(finding.id),
            "work_item_id": str(finding.subject_work_item_id),
            "severity": severity if severity in _radar_vocab.SEVERITIES else severity,
            "resolution": resolution,
        },
    )


@router.post(
    "/workspaces/{workspace_id}/anomalies/{finding_id}/confirm",
    response_model=AnomalyFindingDetail,
    summary="Agree with the finding",
)
def confirm_anomaly(
    workspace_id: uuid.UUID,
    finding_id: uuid.UUID,
    body: AnomalyConfirmRequest,
    db: Session = Depends(get_db),
    context: TenantContext = Depends(RequireContributor),
) -> AnomalyFindingDetail:
    _assert_workspace(context, workspace_id)
    _gate(db, context, "anomalies.confirm")

    finding = _load(db, workspace_id=workspace_id, finding_id=finding_id)
    if finding.status != vocab.STATUS_OPEN:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"This finding was already {finding.status.lower()}. Reopening "
                "a closed finding would discard the decision and the person "
                "who made it."
            ),
        )

    # ARCH40-S1:anomaly-confirm-delegates. The transition moves to the shared
    # review resolution service so the hub and this endpoint cannot diverge.
    # HARDENING-T1:D31. The document audit row below used
    # AuditResourceType.WORK_ITEM and AuditOutcome.SUCCESS, neither of which
    # exists, so this endpoint raised AttributeError on every call. No gate
    # asserted that text (this comment claimed verify_arch34 did; it does
    # not). The REVIEW_ITEM row the shared service adds sits alongside it.
    from app.services.review import resolution as review_resolution

    try:
        review_resolution.resolve_anomaly_transition(
            db,
            finding=finding,
            actor_user_id=context.user_id,
            organization_id=context.organization_id,
            workspace_id=workspace_id,
            payload=review_resolution.ResolvePayload(
                anomaly_verdict=review_resolution.ANOMALY_VERDICT_CONFIRM,
                note=body.note,
            ),
        )
    except review_resolution.ReviewResolutionError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc

    _record_review_audit(
        db,
        context=context,
        workspace_id=workspace_id,
        finding=finding,
        resolution=vocab.STATUS_CONFIRMED,
    )

    audit_service.record(
        db,
        organization_id=context.organization_id,
        actor_id=context.user_id,
        resource_type=AuditResourceType.UPLOADED_FILE,  # HARDENING-T1:D31
        resource_id=finding.subject_work_item_id,
        action=AuditAction.UPDATED,
        outcome=AuditOutcome.ALLOWED,
        details={
            "anomaly_finding_id": str(finding.id),
            "status": vocab.STATUS_CONFIRMED,
            "kind": finding.kind,
            "layer": finding.layer,
        },
    )
    db.commit()
    db.refresh(finding)

    labels = _labels(db, [finding])
    return AnomalyFindingDetail(
        **_summary(finding, labels).model_dump(),
        metrics=dict(finding.metrics or {}),
        evidence=list(finding.evidence or []),
        engine_version=finding.engine_version,
        input_digest=finding.input_digest,
    )


@router.post(
    "/workspaces/{workspace_id}/anomalies/{finding_id}/dismiss",
    response_model=AnomalyFindingDetail,
    summary="Not an anomaly, and here is why",
)
def dismiss_anomaly(
    workspace_id: uuid.UUID,
    finding_id: uuid.UUID,
    body: AnomalyDismissRequest,
    db: Session = Depends(get_db),
    context: TenantContext = Depends(RequireContributor),
) -> AnomalyFindingDetail:
    _assert_workspace(context, workspace_id)
    _gate(db, context, "anomalies.dismiss")

    finding = _load(db, workspace_id=workspace_id, finding_id=finding_id)
    if finding.status != vocab.STATUS_OPEN:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"This finding was already {finding.status.lower()}.",
        )

    # ARCH40-S1:anomaly-dismiss-delegates. See confirm_anomaly above.
    from app.services.review import resolution as review_resolution

    # The suppression is written by the shared service, scoped to the layer
    # that fired, for the reason the pre-ARCH-40 comment here gave: a reviewer
    # dismissing a weak L3 guess has not agreed to never hear about a
    # byte-identical re-upload of the same pair.
    try:
        review_resolution.resolve_anomaly_transition(
            db,
            finding=finding,
            actor_user_id=context.user_id,
            organization_id=context.organization_id,
            workspace_id=workspace_id,
            payload=review_resolution.ResolvePayload(
                anomaly_verdict=review_resolution.ANOMALY_VERDICT_DISMISS,
                note=body.reason,
                ttl_days=body.ttl_days,
            ),
        )
    except review_resolution.ReviewResolutionError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc

    _record_review_audit(
        db,
        context=context,
        workspace_id=workspace_id,
        finding=finding,
        resolution=vocab.STATUS_DISMISSED,
    )

    audit_service.record(
        db,
        organization_id=context.organization_id,
        actor_id=context.user_id,
        resource_type=AuditResourceType.UPLOADED_FILE,  # HARDENING-T1:D31
        resource_id=finding.subject_work_item_id,
        action=AuditAction.UPDATED,
        outcome=AuditOutcome.ALLOWED,
        details={
            "anomaly_finding_id": str(finding.id),
            "status": vocab.STATUS_DISMISSED,
            "kind": finding.kind,
            "layer": finding.layer,
            "suppressed": True,
        },
    )
    db.commit()
    db.refresh(finding)

    labels = _labels(db, [finding])
    return AnomalyFindingDetail(
        **_summary(finding, labels).model_dump(),
        metrics=dict(finding.metrics or {}),
        evidence=list(finding.evidence or []),
        engine_version=finding.engine_version,
        input_digest=finding.input_digest,
    )

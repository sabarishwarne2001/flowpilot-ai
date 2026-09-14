"""ARCH-32 §3.5 — the seven redaction endpoints.

    POST   /workspaces/{wid}/work-items/{id}/redactions        start   [CONTRIBUTOR]
    GET    /workspaces/{wid}/redactions/{jid}                  detail  [VIEWER]
    GET    /workspaces/{wid}/redactions/{jid}/pages/{n}.png    preview [VIEWER]
    PATCH  /workspaces/{wid}/redactions/{jid}/regions/{rid}    toggle  [CONTRIBUTOR]
    POST   /workspaces/{wid}/redactions/{jid}/regions          draw    [CONTRIBUTOR]
    POST   /workspaces/{wid}/redactions/{jid}/apply            approve [ADMIN]
    GET    /workspaces/{wid}/redactions/{jid}/bundle           download[VIEWER]

EVERY ROUTE IS CAPABILITY-GATED, INCLUDING THE READS
====================================================

Same reasoning ARCH-31 recorded, and it is stronger here. Gating only the
writes would let a tenant without the capability start nothing but still read
back a completed job's regions — which is a map of where every identifier in
their corpus sits, page by page. The detection output IS the product.

WHY APPLY IS ADMIN AND EVERYTHING ELSE IS NOT
=============================================

Toggling a region is reviewing. Drawing a box is reviewing. Both are the daily
work of whoever prepares documents for disclosure, and putting them behind
ADMIN means the person who does the job cannot do the job.

Applying is different in kind: it publishes a file that leaves the perimeter
and it stamps a named approver onto a row that `ck_rj_completed_is_sealed`
will not let anyone forge afterwards. §3.5 says "ADMIN or redaction approver
grant"; the grant does not exist yet, so this is ADMIN, and adding the grant
later widens the gate rather than reshaping it.

WHY THE PREVIEW IS NOT CACHED
=============================

`/pages/{n}.png` renders the SOURCE — the unredacted document — at preview
DPI. Caching that anywhere (disk, CDN, browser) would put an unredacted
rendering of a document somebody is in the middle of redacting into a store
with a different lifetime than the job. The response carries `no-store` and
the bytes are built per request. It is slower and that is the correct trade.

REFUSALS
========

`CapabilityRequiredError` (402) and the service's own errors are rendered by
`app/core/exception_handlers.py` into the ARCH-01 envelope, so the console's
`ApiError` handling needs no new branch. Nothing is caught and re-raised as
an `HTTPException` with a bare string here except genuine 404s.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Response, status
from sqlalchemy.orm import Session

from app.api import capability_gate
from app.api.deps import RequireWorkspaceRole, TenantContext, get_db
from app.core import entitlements
from app.models.workspace import WorkspaceRole
from app.schemas.redaction import (
    BundleResponse,
    LeakCheckResponse,
    RedactionJobResponse,
    RedactionStartRequest,
    RegionCreateRequest,
    RegionResponse,
    RegionToggleRequest,
)
from app.services.redaction import redaction_service
from app.services.redaction.vocabulary import (
    CHECKSUM_DETECTORS,
    PROFILE_KEYS,
    UnknownProfileError,
    profile_mentions_names,
)

logger = logging.getLogger("app.api.v1.redactions")

router = APIRouter(tags=["Redaction"])

RequireViewer = RequireWorkspaceRole(WorkspaceRole.VIEWER)
RequireContributor = RequireWorkspaceRole(WorkspaceRole.CONTRIBUTOR)
RequireAdmin = RequireWorkspaceRole(WorkspaceRole.ADMIN)

CAPABILITY = entitlements.REDACTION_CAPABILITY


def _gate(db: Session, context: TenantContext, operation: str) -> None:
    capability_gate.require_capability(
        db, context=context, capability_key=CAPABILITY, operation=operation
    )


def _assert_workspace(context: TenantContext, workspace_id: uuid.UUID) -> None:
    """ARCH-02. A mismatch is a bug, and it never resolves in the caller's favour."""
    if context.workspace_id != workspace_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Workspace not found."
        )


def _region_response(region: Any) -> RegionResponse:
    payload = RegionResponse.model_validate(region)
    return payload.model_copy(
        update={"checksum_validated": region.detector in CHECKSUM_DETECTORS}
    )


def _job_response(job: Any) -> RedactionJobResponse:
    detail = job.leak_check_detail or None
    sentence = None
    if detail is not None:
        # Rebuilt from the stored summary rather than stored as prose, so a
        # wording change reaches historical jobs too.
        from app.services.redaction.leakcheck import LeakReport

        sentence = LeakReport(
            passed=bool(detail.get("passed")),
            pages_checked=int(detail.get("pages_checked") or 0),
            secrets_checked=int(detail.get("secrets_checked") or 0),
            text_hits=tuple(detail.get("text_hit_pages") or ()),
            forbidden_keys=tuple(detail.get("forbidden_keys") or ()),
            stream_hits=int(detail.get("stream_hits") or 0),
        ).sentence()

    response = RedactionJobResponse.model_validate(job)
    return response.model_copy(
        update={
            "regions": [
                _region_response(r)
                for r in sorted(
                    job.regions,
                    key=lambda r: (r.page_number, float(r.confidence) * -1, float(r.x0)),
                )
            ],
            "leak_check": LeakCheckResponse(
                passed=job.leak_check_passed, sentence=sentence, detail=detail
            ),
            "names_limit_applies": profile_mentions_names(job.profile_key),
            "available_profiles": list(PROFILE_KEYS),
        }
    )


# ---------------------------------------------------------------------------
# Start
# ---------------------------------------------------------------------------


@router.post(
    "/workspaces/{workspace_id}/work-items/{work_item_id}/redactions",
    response_model=RedactionJobResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def start_redaction(
    payload: RedactionStartRequest,
    workspace_id: uuid.UUID = Path(...),
    work_item_id: uuid.UUID = Path(...),
    context: TenantContext = Depends(RequireContributor),
    db: Session = Depends(get_db),
) -> RedactionJobResponse:
    _assert_workspace(context, workspace_id)
    _gate(db, context, "redaction.start")

    try:
        job = redaction_service.start_job(
            db,
            organization_id=context.organization_id,
            workspace_id=workspace_id,
            work_item_id=work_item_id,
            user_id=context.user_id,
            profile_key=payload.profile_key,
            render_dpi=payload.render_dpi,
            restore_text_layer=payload.restore_text_layer,
        )
    except UnknownProfileError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    except redaction_service.JobNotFound as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc
    except redaction_service.InvalidJobState as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc

    db.commit()
    db.refresh(job)
    return _job_response(job)


# ---------------------------------------------------------------------------
# Detail
# ---------------------------------------------------------------------------


@router.get(
    "/workspaces/{workspace_id}/redactions/{job_id}",
    response_model=RedactionJobResponse,
)
def get_redaction(
    workspace_id: uuid.UUID = Path(...),
    job_id: uuid.UUID = Path(...),
    context: TenantContext = Depends(RequireViewer),
    db: Session = Depends(get_db),
) -> RedactionJobResponse:
    _assert_workspace(context, workspace_id)
    _gate(db, context, "redaction.read")
    try:
        job = redaction_service.load_job(db, job_id=job_id, workspace_id=workspace_id)
    except redaction_service.JobNotFound as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc
    return _job_response(job)


# ---------------------------------------------------------------------------
# Preview
# ---------------------------------------------------------------------------


@router.get("/workspaces/{workspace_id}/redactions/{job_id}/pages/{page_number}.png")
def preview_page(
    workspace_id: uuid.UUID = Path(...),
    job_id: uuid.UUID = Path(...),
    page_number: int = Path(..., ge=1),
    dpi: int = Query(default=110, ge=75, le=300),
    burn: bool = Query(
        default=False,
        description=(
            "When true, renders through the SAME burn path the apply job "
            "uses, so the preview is what will be produced."
        ),
    ),
    context: TenantContext = Depends(RequireViewer),
    db: Session = Depends(get_db),
) -> Response:
    _assert_workspace(context, workspace_id)
    _gate(db, context, "redaction.preview")
    try:
        job = redaction_service.load_job(db, job_id=job_id, workspace_id=workspace_id)
        png = redaction_service.render_source_page_png(
            db, job=job, page_number=page_number, dpi=dpi, burn=burn
        )
    except redaction_service.JobNotFound as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc
    except redaction_service.InvalidJobState as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc

    return Response(
        content=png,
        media_type="image/png",
        headers={
            # See the module header. This is a rendering of the UNREDACTED
            # document; it must not outlive the request that asked for it.
            "Cache-Control": "no-store, no-cache, must-revalidate, private",
            "Pragma": "no-cache",
        },
    )


# ---------------------------------------------------------------------------
# Regions
# ---------------------------------------------------------------------------


@router.patch(
    "/workspaces/{workspace_id}/redactions/{job_id}/regions/{region_id}",
    response_model=RegionResponse,
)
def toggle_region(
    payload: RegionToggleRequest,
    workspace_id: uuid.UUID = Path(...),
    job_id: uuid.UUID = Path(...),
    region_id: uuid.UUID = Path(...),
    context: TenantContext = Depends(RequireContributor),
    db: Session = Depends(get_db),
) -> RegionResponse:
    _assert_workspace(context, workspace_id)
    _gate(db, context, "redaction.toggle_region")
    try:
        job = redaction_service.load_job(db, job_id=job_id, workspace_id=workspace_id)
        region = redaction_service.set_region_enabled(
            db,
            job=job,
            region_id=region_id,
            enabled=payload.enabled,
            user_id=context.user_id,
        )
    except redaction_service.JobNotFound as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc
    except redaction_service.InvalidJobState as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc
    db.commit()
    db.refresh(region)
    return _region_response(region)


@router.post(
    "/workspaces/{workspace_id}/redactions/{job_id}/regions",
    response_model=RegionResponse,
    status_code=status.HTTP_201_CREATED,
)
def add_region(
    payload: RegionCreateRequest,
    workspace_id: uuid.UUID = Path(...),
    job_id: uuid.UUID = Path(...),
    context: TenantContext = Depends(RequireContributor),
    db: Session = Depends(get_db),
) -> RegionResponse:
    _assert_workspace(context, workspace_id)
    _gate(db, context, "redaction.draw_region")
    try:
        job = redaction_service.load_job(db, job_id=job_id, workspace_id=workspace_id)
        region = redaction_service.add_manual_region(
            db,
            job=job,
            page_number=payload.page_number,
            x0=payload.x0,
            y0=payload.y0,
            x1=payload.x1,
            y1=payload.y1,
            # `ck_rr_manual_has_author` refuses the row without this, so a
            # manual box is always attributable.
            user_id=context.user_id,
        )
    except redaction_service.JobNotFound as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc
    except redaction_service.InvalidJobState as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    db.commit()
    db.refresh(region)
    return _region_response(region)


# ---------------------------------------------------------------------------
# Apply
# ---------------------------------------------------------------------------


@router.post(
    "/workspaces/{workspace_id}/redactions/{job_id}/apply",
    response_model=RedactionJobResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def apply_redaction(
    workspace_id: uuid.UUID = Path(...),
    job_id: uuid.UUID = Path(...),
    context: TenantContext = Depends(RequireAdmin),
    db: Session = Depends(get_db),
) -> RedactionJobResponse:
    _assert_workspace(context, workspace_id)
    _gate(db, context, "redaction.apply")
    try:
        job = redaction_service.load_job(db, job_id=job_id, workspace_id=workspace_id)
        job = redaction_service.approve_and_enqueue_apply(
            db, job=job, user_id=context.user_id
        )
    except redaction_service.JobNotFound as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc
    except redaction_service.InvalidJobState as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc
    db.commit()
    db.refresh(job)
    return _job_response(job)


# ---------------------------------------------------------------------------
# Bundle
# ---------------------------------------------------------------------------


@router.get(
    "/workspaces/{workspace_id}/redactions/{job_id}/bundle",
    response_model=BundleResponse,
)
def get_bundle(
    workspace_id: uuid.UUID = Path(...),
    job_id: uuid.UUID = Path(...),
    context: TenantContext = Depends(RequireViewer),
    db: Session = Depends(get_db),
) -> BundleResponse:
    _assert_workspace(context, workspace_id)
    _gate(db, context, "redaction.bundle")
    try:
        job = redaction_service.load_job(db, job_id=job_id, workspace_id=workspace_id)
        payload = redaction_service.bundle_urls(db, job=job)
    except redaction_service.JobNotFound as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc
    except redaction_service.InvalidJobState as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc
    return BundleResponse(**payload)
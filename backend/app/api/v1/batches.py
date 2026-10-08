"""Phase 1 — the batch processing & document dispatch engine API.

Every route is gated on capability.batch_dispatch (402 CAPABILITY_REQUIRED without it). Reads need
VIEWER; creating batches, healing, dispatching, retrying and requesting packages need CONTRIBUTOR;
the dispatch policy is a workspace setting and needs ADMIN. A package download and a verification
are audited, as every act that changes documents or tags is.

    /workspaces/{ws}/processing-batches                      list, create
    /workspaces/{ws}/processing-batches/{id}                 detail (documents assessed live), rename, archive, delete
    /workspaces/{ws}/processing-batches/{id}/documents       add; DELETE .../{work_item_id} removes
    /workspaces/{ws}/processing-batches/{id}/analytics       confidence, schema health, lanes, throughput
    /workspaces/{ws}/processing-batches/{id}/heal            apply schema healing
    /workspaces/{ws}/processing-batches/{id}/dispatch        record lanes (and tag documents)
    /workspaces/{ws}/processing-batches/{id}/retry-failed    re-run extraction for failed documents
    /workspaces/{ws}/schema-healing/{event_id}/revert        undo one healing
    /workspaces/{ws}/dispatch-policy                         read, replace
    /workspaces/{ws}/export-packages                         list, request
    /workspaces/{ws}/export-packages/verify                  check an uploaded package
    /workspaces/{ws}/export-packages/{id}                    one package; /manifest; /download
"""

from __future__ import annotations

import tempfile
import uuid
from datetime import datetime, timezone
from typing import Any, Iterator, Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, Response, UploadFile, status
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api import capability_gate
from app.api.deps import RequireWorkspaceRole, TenantContext, get_db
from app.core import entitlements
from app.core.config import settings
from app.models.audit_log import AuditAction, AuditOutcome, AuditResourceType
from app.models.batches import ExportPackage, ProcessingBatch, SchemaHealingEvent
from app.models.work_item import WorkItem
from app.models.workspace import WorkspaceRole
from app.schemas.batches import (
    BatchAnalytics,
    BatchCreate,
    BatchDetail,
    BatchDocument,
    BatchDocumentsAdd,
    BatchList,
    BatchProgress,
    BatchSummary,
    BatchUpdate,
    DispatchPolicyIn,
    DispatchPolicyOut,
    DispatchResult,
    HealingEventOut,
    HealRequest,
    HealResult,
    PackageCreate,
    PackageList,
    PackageManifestOut,
    PackageOut,
    RetryResult,
    VerifyResult,
)
from app.services import audit_service
from app.services.batches import dispatch as dsp, healing, packages, service
from app.services.batches.service import BatchError, Progress

router = APIRouter(tags=["Batch Operations"])

RequireViewer = RequireWorkspaceRole(WorkspaceRole.VIEWER)
RequireContributor = RequireWorkspaceRole(WorkspaceRole.CONTRIBUTOR)
RequireAdmin = RequireWorkspaceRole(WorkspaceRole.ADMIN)
CAPABILITY = entitlements.BATCH_DISPATCH_CAPABILITY
_CHUNK = 1024 * 1024


def _gate(db: Session, context: TenantContext, operation: str) -> None:
    capability_gate.require_capability(db, context=context, capability_key=CAPABILITY, operation=operation)


def _ws(context: TenantContext, workspace_id: uuid.UUID) -> None:
    if context.workspace_id != workspace_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace not found.")


def _error(exc: BatchError | packages.PackageError | healing.HealingRefused, code: int = 422) -> HTTPException:
    return HTTPException(
        status_code=getattr(exc, "status_code", code),
        detail={"code": exc.code, "message": exc.message},
    )


def _batch_or_404(db: Session, workspace_id: uuid.UUID, batch_id: uuid.UUID) -> ProcessingBatch:
    batch = db.execute(
        select(ProcessingBatch).where(ProcessingBatch.id == batch_id, ProcessingBatch.workspace_id == workspace_id)
    ).scalar_one_or_none()
    if batch is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Batch not found.")
    return batch


def _package_or_404(db: Session, workspace_id: uuid.UUID, package_id: uuid.UUID) -> ExportPackage:
    package = db.execute(
        select(ExportPackage).where(ExportPackage.id == package_id, ExportPackage.workspace_id == workspace_id)
    ).scalar_one_or_none()
    if package is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Export package not found.")
    return package


def _summary(batch: ProcessingBatch, progress: Optional[Progress]) -> BatchSummary:
    p = progress or Progress()
    return BatchSummary(
        id=batch.id, name=batch.name, description=batch.description, source=batch.source, status=batch.status,
        ingestion_batch_id=batch.ingestion_batch_id, created_by_user_id=batch.created_by_user_id,
        created_at=batch.created_at, updated_at=batch.updated_at, dispatched_at=batch.dispatched_at,
        progress=BatchProgress(
            documents=p.documents, queued=p.queued, processing=p.processing, completed=p.completed,
            failed=p.failed, percent=p.percent, straight_through=p.straight_through, review=p.review,
            exception=p.exception, mean_confidence=p.mean_confidence,
        ),
    )


def _policy_out(policy: dsp.Policy) -> DispatchPolicyOut:
    return DispatchPolicyOut(
        straight_through_min_confidence=float(policy.straight_through_min),
        review_min_confidence=float(policy.review_min),
        require_required_fields=policy.require_required_fields,
        tag_documents=policy.tag_documents,
        is_default=policy.is_default,
        updated_at=policy.updated_at,
    )


def _package_out(package: ExportPackage) -> PackageOut:
    return PackageOut.model_validate(package, from_attributes=True)


def _audit(
    db: Session,
    context: TenantContext,
    *,
    resource_type: AuditResourceType,
    resource_id: Optional[uuid.UUID],
    action: AuditAction,
    details: dict[str, Any],
    outcome: AuditOutcome = AuditOutcome.ALLOWED,
) -> None:
    audit_service.record(
        db,
        organization_id=context.organization_id,
        workspace_id=context.workspace_id,
        actor_id=context.user_id,
        resource_type=resource_type,
        resource_id=resource_id,
        action=action,
        outcome=outcome,
        details=details,
    )


# ------------------------------------------------------------------ batches

@router.get("/workspaces/{workspace_id}/processing-batches", response_model=BatchList)
def list_batches(
    workspace_id: uuid.UUID,
    batch_status: Optional[str] = Query(default=None, alias="status", pattern="^(ACTIVE|ARCHIVED)$"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    context: TenantContext = Depends(RequireViewer),
) -> BatchList:
    _ws(context, workspace_id)
    _gate(db, context, "batches.list")
    query = select(ProcessingBatch).where(ProcessingBatch.workspace_id == workspace_id)
    if batch_status:
        query = query.where(ProcessingBatch.status == batch_status)
    total = db.execute(select(func.count()).select_from(query.subquery())).scalar_one()
    batches = list(
        db.execute(
            query.order_by(ProcessingBatch.created_at.desc(), ProcessingBatch.id.desc()).limit(limit).offset(offset)
        ).scalars()
    )
    progress = service.progress_for(db, [b.id for b in batches])
    return BatchList(items=[_summary(b, progress.get(b.id)) for b in batches], total=int(total))


@router.post(
    "/workspaces/{workspace_id}/processing-batches", response_model=BatchSummary, status_code=status.HTTP_201_CREATED
)
def create_batch(
    workspace_id: uuid.UUID,
    payload: BatchCreate,
    db: Session = Depends(get_db),
    context: TenantContext = Depends(RequireContributor),
) -> BatchSummary:
    _ws(context, workspace_id)
    _gate(db, context, "batches.create")
    try:
        batch = service.create_batch(
            db, workspace_id=workspace_id, organization_id=context.organization_id, user_id=context.user_id,
            name=payload.name, description=payload.description, work_item_ids=payload.work_item_ids,
            ingestion_batch_id=payload.ingestion_batch_id,
        )
    except BatchError as exc:
        db.rollback()
        raise _error(exc) from exc
    _audit(db, context, resource_type=AuditResourceType.PROCESSING_BATCH, resource_id=batch.id,
           action=AuditAction.CREATED, details={"name": batch.name, "source": batch.source})
    db.commit()
    return _summary(batch, service.progress_for(db, [batch.id]).get(batch.id))


@router.get("/workspaces/{workspace_id}/processing-batches/{batch_id}", response_model=BatchDetail)
def get_batch(
    workspace_id: uuid.UUID,
    batch_id: uuid.UUID,
    db: Session = Depends(get_db),
    context: TenantContext = Depends(RequireViewer),
) -> BatchDetail:
    _ws(context, workspace_id)
    _gate(db, context, "batches.read")
    batch = _batch_or_404(db, workspace_id, batch_id)
    assessment = service.assess(db, batch=batch)
    documents = []
    for d in assessment.documents:
        score = d.confidence
        documents.append(BatchDocument(
            work_item_id=d.item.id, original_filename=d.item.original_filename, status=d.item.status,
            document_type=d.healing.document_type, added_at=d.membership.added_at,
            confidence=score.confidence if score else None,
            verification_status=score.verification_status if score else None,
            lane=d.decision.lane, reasons=list(d.decision.reasons),
            dispatched_lane=d.membership.lane, dispatched_at=d.membership.dispatched_at,
            schema_key=d.healing.schema_key, schema_label=d.healing.schema_label, schema_state=d.healing.state,
            completeness=d.healing.completeness,
            changes=[c for c in d.healing.changes_json()], issues=[i for i in d.healing.issues_json()],
            failure_reason=d.item.failure_reason,
        ))
    return BatchDetail(
        batch=_summary(batch, service.progress_for(db, [batch.id]).get(batch.id)),
        policy=_policy_out(assessment.policy),
        documents=documents,
    )


@router.patch("/workspaces/{workspace_id}/processing-batches/{batch_id}", response_model=BatchSummary)
def update_batch(
    workspace_id: uuid.UUID,
    batch_id: uuid.UUID,
    payload: BatchUpdate,
    db: Session = Depends(get_db),
    context: TenantContext = Depends(RequireContributor),
) -> BatchSummary:
    _ws(context, workspace_id)
    _gate(db, context, "batches.update")
    batch = _batch_or_404(db, workspace_id, batch_id)
    changed: dict[str, Any] = {}
    if payload.name is not None:
        name = " ".join(payload.name.split())
        if not name:
            raise HTTPException(status_code=422, detail={"code": "NAME_REQUIRED", "message": "Give the batch a name."})
        batch.name, changed["name"] = name[:120], name[:120]
    if payload.description is not None:
        batch.description, changed["description"] = payload.description.strip(), True
    if payload.status is not None and payload.status != batch.status:
        batch.status, changed["status"] = payload.status, payload.status
    batch.updated_at = datetime.now(timezone.utc)
    _audit(db, context, resource_type=AuditResourceType.PROCESSING_BATCH, resource_id=batch.id,
           action=AuditAction.ARCHIVED if changed.get("status") == "ARCHIVED" else AuditAction.UPDATED,
           details=changed)
    db.commit()
    return _summary(batch, service.progress_for(db, [batch.id]).get(batch.id))


@router.delete(
    "/workspaces/{workspace_id}/processing-batches/{batch_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
def delete_batch(
    workspace_id: uuid.UUID,
    batch_id: uuid.UUID,
    db: Session = Depends(get_db),
    context: TenantContext = Depends(RequireContributor),
) -> Response:
    """Deletes the batch (its grouping and recorded lanes). Its documents are not touched."""
    _ws(context, workspace_id)
    _gate(db, context, "batches.delete")
    batch = _batch_or_404(db, workspace_id, batch_id)
    _audit(db, context, resource_type=AuditResourceType.PROCESSING_BATCH, resource_id=batch.id,
           action=AuditAction.DELETED, details={"name": batch.name})
    db.delete(batch)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/workspaces/{workspace_id}/processing-batches/{batch_id}/documents", response_model=BatchSummary)
def add_batch_documents(
    workspace_id: uuid.UUID,
    batch_id: uuid.UUID,
    payload: BatchDocumentsAdd,
    db: Session = Depends(get_db),
    context: TenantContext = Depends(RequireContributor),
) -> BatchSummary:
    _ws(context, workspace_id)
    _gate(db, context, "batches.add_documents")
    batch = _batch_or_404(db, workspace_id, batch_id)
    try:
        added = service.add_documents(db, batch=batch, work_item_ids=payload.work_item_ids)
    except BatchError as exc:
        db.rollback()
        raise _error(exc) from exc
    _audit(db, context, resource_type=AuditResourceType.PROCESSING_BATCH, resource_id=batch.id,
           action=AuditAction.UPDATED, details={"added_documents": added})
    db.commit()
    return _summary(batch, service.progress_for(db, [batch.id]).get(batch.id))


@router.delete(
    "/workspaces/{workspace_id}/processing-batches/{batch_id}/documents/{work_item_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
def remove_batch_document(
    workspace_id: uuid.UUID,
    batch_id: uuid.UUID,
    work_item_id: uuid.UUID,
    db: Session = Depends(get_db),
    context: TenantContext = Depends(RequireContributor),
) -> Response:
    _ws(context, workspace_id)
    _gate(db, context, "batches.remove_document")
    batch = _batch_or_404(db, workspace_id, batch_id)
    if not service.remove_document(db, batch=batch, work_item_id=work_item_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="That document is not in this batch.")
    _audit(db, context, resource_type=AuditResourceType.PROCESSING_BATCH, resource_id=batch.id,
           action=AuditAction.UPDATED, details={"removed_document": str(work_item_id)})
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/workspaces/{workspace_id}/processing-batches/{batch_id}/analytics", response_model=BatchAnalytics)
def batch_analytics(
    workspace_id: uuid.UUID,
    batch_id: uuid.UUID,
    db: Session = Depends(get_db),
    context: TenantContext = Depends(RequireViewer),
) -> BatchAnalytics:
    _ws(context, workspace_id)
    _gate(db, context, "batches.analytics")
    batch = _batch_or_404(db, workspace_id, batch_id)
    return BatchAnalytics.model_validate(service.analytics(service.assess(db, batch=batch)))


@router.post("/workspaces/{workspace_id}/processing-batches/{batch_id}/heal", response_model=HealResult)
def heal_batch(
    workspace_id: uuid.UUID,
    batch_id: uuid.UUID,
    payload: HealRequest,
    db: Session = Depends(get_db),
    context: TenantContext = Depends(RequireContributor),
) -> HealResult:
    _ws(context, workspace_id)
    _gate(db, context, "batches.heal")
    batch = _batch_or_404(db, workspace_id, batch_id)
    result = service.heal(db, batch=batch, user_id=context.user_id, work_item_ids=payload.work_item_ids or None)
    _audit(db, context, resource_type=AuditResourceType.PROCESSING_BATCH, resource_id=batch.id,
           action=AuditAction.UPDATED,
           details={"operation": "schema_healing", "healed": result["healed"], "refused": result["refused"]})
    db.commit()
    return HealResult.model_validate(result)


@router.post("/workspaces/{workspace_id}/processing-batches/{batch_id}/dispatch", response_model=DispatchResult)
def dispatch_batch(
    workspace_id: uuid.UUID,
    batch_id: uuid.UUID,
    db: Session = Depends(get_db),
    context: TenantContext = Depends(RequireContributor),
) -> DispatchResult:
    _ws(context, workspace_id)
    _gate(db, context, "batches.dispatch")
    batch = _batch_or_404(db, workspace_id, batch_id)
    result = service.dispatch(db, batch=batch, user_id=context.user_id)
    _audit(db, context, resource_type=AuditResourceType.PROCESSING_BATCH, resource_id=batch.id,
           action=AuditAction.UPDATED, details={"operation": "dispatch", **{k: val for k, val in result.items()
                                                                            if k != "dispatched_at"}})
    db.commit()
    return DispatchResult.model_validate(result)


@router.post("/workspaces/{workspace_id}/processing-batches/{batch_id}/retry-failed", response_model=RetryResult)
def retry_failed(
    workspace_id: uuid.UUID,
    batch_id: uuid.UUID,
    db: Session = Depends(get_db),
    context: TenantContext = Depends(RequireContributor),
) -> RetryResult:
    _ws(context, workspace_id)
    _gate(db, context, "batches.retry_failed")
    batch = _batch_or_404(db, workspace_id, batch_id)
    result = service.retry_failed(db, batch=batch)
    _audit(db, context, resource_type=AuditResourceType.PROCESSING_BATCH, resource_id=batch.id,
           action=AuditAction.UPDATED, details={"operation": "retry_failed", "requeued": result["requeued"]})
    db.commit()
    return RetryResult.model_validate(result)


@router.get(
    "/workspaces/{workspace_id}/work-items/{work_item_id}/schema-healing", response_model=list[HealingEventOut]
)
def document_healing_history(
    workspace_id: uuid.UUID,
    work_item_id: uuid.UUID,
    db: Session = Depends(get_db),
    context: TenantContext = Depends(RequireViewer),
) -> list[HealingEventOut]:
    _ws(context, workspace_id)
    _gate(db, context, "batches.healing_history")
    if db.execute(
        select(WorkItem.id).where(WorkItem.id == work_item_id, WorkItem.workspace_id == workspace_id)
    ).scalar_one_or_none() is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found.")
    events = db.execute(
        select(SchemaHealingEvent)
        .where(SchemaHealingEvent.workspace_id == workspace_id, SchemaHealingEvent.work_item_id == work_item_id)
        .order_by(SchemaHealingEvent.applied_at.desc())
    ).scalars()
    return [HealingEventOut.model_validate(e, from_attributes=True) for e in events]


@router.post("/workspaces/{workspace_id}/schema-healing/{event_id}/revert", response_model=HealingEventOut)
def revert_healing(
    workspace_id: uuid.UUID,
    event_id: uuid.UUID,
    db: Session = Depends(get_db),
    context: TenantContext = Depends(RequireContributor),
) -> HealingEventOut:
    _ws(context, workspace_id)
    _gate(db, context, "batches.healing_revert")
    event = db.execute(
        select(SchemaHealingEvent).where(SchemaHealingEvent.id == event_id, SchemaHealingEvent.workspace_id == workspace_id)
    ).scalar_one_or_none()
    if event is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Healing not found.")
    item = db.execute(
        select(WorkItem).where(WorkItem.id == event.work_item_id, WorkItem.workspace_id == workspace_id)
    ).scalar_one()
    try:
        healing.revert(db, event=event, work_item=item, organization_id=context.organization_id,
                       user_id=context.user_id)
    except healing.HealingRefused as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail={"code": exc.code, "message": exc.message}) from exc
    db.commit()
    return HealingEventOut.model_validate(event, from_attributes=True)


# ------------------------------------------------------------------ dispatch policy

@router.get("/workspaces/{workspace_id}/dispatch-policy", response_model=DispatchPolicyOut)
def get_dispatch_policy(
    workspace_id: uuid.UUID,
    db: Session = Depends(get_db),
    context: TenantContext = Depends(RequireViewer),
) -> DispatchPolicyOut:
    _ws(context, workspace_id)
    _gate(db, context, "batches.policy_read")
    return _policy_out(dsp.policy_for(db, workspace_id))


@router.put("/workspaces/{workspace_id}/dispatch-policy", response_model=DispatchPolicyOut)
def put_dispatch_policy(
    workspace_id: uuid.UUID,
    payload: DispatchPolicyIn,
    db: Session = Depends(get_db),
    context: TenantContext = Depends(RequireAdmin),
) -> DispatchPolicyOut:
    _ws(context, workspace_id)
    _gate(db, context, "batches.policy_update")
    dsp.save_policy(
        db, workspace_id=workspace_id, straight_through_min=payload.straight_through_min_confidence,
        review_min=payload.review_min_confidence, require_required_fields=payload.require_required_fields,
        tag_documents=payload.tag_documents, user_id=context.user_id,
    )
    _audit(db, context, resource_type=AuditResourceType.WORKSPACE, resource_id=workspace_id,
           action=AuditAction.UPDATED,
           details={"dispatch_policy": {
               "straight_through_min_confidence": str(payload.straight_through_min_confidence),
               "review_min_confidence": str(payload.review_min_confidence),
               "require_required_fields": payload.require_required_fields,
               "tag_documents": payload.tag_documents,
           }})
    db.commit()
    return _policy_out(dsp.policy_for(db, workspace_id))


# ------------------------------------------------------------------ export packages

@router.get("/workspaces/{workspace_id}/export-packages", response_model=PackageList)
def list_packages(
    workspace_id: uuid.UUID,
    batch_id: Optional[uuid.UUID] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    context: TenantContext = Depends(RequireViewer),
) -> PackageList:
    _ws(context, workspace_id)
    _gate(db, context, "packages.list")
    query = select(ExportPackage).where(ExportPackage.workspace_id == workspace_id)
    if batch_id is not None:
        query = query.where(ExportPackage.batch_id == batch_id)
    total = db.execute(select(func.count()).select_from(query.subquery())).scalar_one()
    rows = db.execute(
        query.order_by(ExportPackage.created_at.desc(), ExportPackage.id.desc()).limit(limit).offset(offset)
    ).scalars()
    return PackageList(items=[_package_out(p) for p in rows], total=int(total))


@router.post("/workspaces/{workspace_id}/export-packages", response_model=PackageOut, status_code=status.HTTP_202_ACCEPTED)
def request_package(
    workspace_id: uuid.UUID,
    payload: PackageCreate,
    db: Session = Depends(get_db),
    context: TenantContext = Depends(RequireContributor),
) -> PackageOut:
    _ws(context, workspace_id)
    _gate(db, context, "packages.create")
    batch = _batch_or_404(db, workspace_id, payload.batch_id) if payload.batch_id else None
    try:
        package = packages.request_package(
            db, workspace_id=workspace_id, organization_id=context.organization_id, user_id=context.user_id,
            name=payload.name, batch=batch, work_item_ids=payload.work_item_ids,
            include_originals=payload.include_originals,
        )
    except packages.PackageError as exc:
        db.rollback()
        raise _error(exc) from exc
    _audit(db, context, resource_type=AuditResourceType.EXPORT_PACKAGE, resource_id=package.id,
           action=AuditAction.EXPORT_REQUESTED,
           details={"name": package.name, "documents": package.document_count,
                    "include_originals": package.include_originals,
                    "batch_id": str(package.batch_id) if package.batch_id else None})
    db.commit()
    return _package_out(package)


@router.post("/workspaces/{workspace_id}/export-packages/verify", response_model=VerifyResult)
def verify_package(
    workspace_id: uuid.UUID,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    context: TenantContext = Depends(RequireViewer),
) -> VerifyResult:
    """Check an export package someone holds against its checksums and this workspace's record."""
    _ws(context, workspace_id)
    _gate(db, context, "packages.verify")
    limit = int(settings.MAX_UPLOAD_SIZE) * 4
    with tempfile.SpooledTemporaryFile(max_size=32 * 1024 * 1024) as handle:
        size = 0
        while True:
            chunk = file.file.read(_CHUNK)
            if not chunk:
                break
            size += len(chunk)
            if size > limit:
                raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                                    detail={"code": "TOO_LARGE", "message": "That file is too large to verify here."})
            handle.write(chunk)
        result = packages.verify(db, workspace_id=workspace_id, handle=handle)
    package_id = None
    try:
        package_id = uuid.UUID(result["package_id"]) if result.get("package_id") else None
    except ValueError:
        package_id = None
    _audit(db, context, resource_type=AuditResourceType.EXPORT_PACKAGE, resource_id=package_id,
           action=AuditAction.ACCESSED,
           details={"operation": "verify", "verdict": result["verdict"], "archive_sha256": result["archive_sha256"],
                    "problems": len(result.get("problems") or [])})
    db.commit()
    return VerifyResult.model_validate(result)


@router.get("/workspaces/{workspace_id}/export-packages/{package_id}", response_model=PackageOut)
def get_package(
    workspace_id: uuid.UUID,
    package_id: uuid.UUID,
    db: Session = Depends(get_db),
    context: TenantContext = Depends(RequireViewer),
) -> PackageOut:
    _ws(context, workspace_id)
    _gate(db, context, "packages.read")
    return _package_out(_package_or_404(db, workspace_id, package_id))


@router.get("/workspaces/{workspace_id}/export-packages/{package_id}/manifest", response_model=PackageManifestOut)
def package_manifest(
    workspace_id: uuid.UUID,
    package_id: uuid.UUID,
    db: Session = Depends(get_db),
    context: TenantContext = Depends(RequireViewer),
) -> PackageManifestOut:
    """The integrity report: every file of the package with its SHA-256 and size."""
    _ws(context, workspace_id)
    _gate(db, context, "packages.manifest")
    package = _package_or_404(db, workspace_id, package_id)
    return PackageManifestOut.model_validate({"package": _package_out(package), "files": package.manifest or []})


@router.get("/workspaces/{workspace_id}/export-packages/{package_id}/download")
def download_package(
    workspace_id: uuid.UUID,
    package_id: uuid.UUID,
    db: Session = Depends(get_db),
    context: TenantContext = Depends(RequireContributor),
) -> StreamingResponse:
    from app.core.storage import get_storage_driver

    _ws(context, workspace_id)
    _gate(db, context, "packages.download")
    package = _package_or_404(db, workspace_id, package_id)
    now = datetime.now(timezone.utc)
    if package.status == "READY" and package.expires_at is not None and package.expires_at <= now:
        packages.expire(db, now=now)
        db.commit()
        db.refresh(package)
    if package.status != "READY" or not package.storage_key:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": f"PACKAGE_{package.status}",
                    "message": "This package has expired; request a new one." if package.status == "EXPIRED"
                    else "This package is not ready to download."},
        )
    package.download_count = (package.download_count or 0) + 1
    package.last_downloaded_at = now
    _audit(db, context, resource_type=AuditResourceType.EXPORT_PACKAGE, resource_id=package.id,
           action=AuditAction.EXPORTED, details={"operation": "download", "package_sha256": package.package_sha256})
    db.commit()
    key, digest, size = package.storage_key, package.package_sha256, package.size_bytes
    filename = f"{packages.folder_name(package)}.zip"
    driver = get_storage_driver()

    def body() -> Iterator[bytes]:
        yield from driver.iter_chunks(key, chunk_size=_CHUNK)

    return StreamingResponse(
        body(),
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Content-Length": str(size),
            "X-Content-SHA256": digest or "",
            "Cache-Control": "no-store",
        },
    )


__all__ = ["router"]

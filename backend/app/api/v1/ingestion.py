"""ARCH-38 — batch ingestion API.

FOUR ROUTERS, MOUNTED AT THREE PREFIXES
=======================================

* `session_router`  -> /workspaces/{id}/upload-sessions
* `batch_router`    -> /workspaces/{id}/ingestion-batches
* `work_item_router`-> /workspaces/{id}/work-items   (bulk, tags)
* `preset_router`   -> /workspaces/{id}/document-presets

WHY PARTS ARRIVE HERE RATHER THAN AT MINIO
==========================================

ARCH-08 §B.11 closed the presigned-URL question: a presigned URL is an
unauthenticated bearer capability, and files must not be reachable without
passing the tenant guard. Every part therefore passes
`RequireWorkspaceContributor` and a workspace-scoped session lookup before a
byte reaches storage. One extra hop per part is the price; the alternative is a
URL that writes into a tenant's prefix with no session behind it.

EVERY DESTRUCTIVE PATH IS AUDITED
=================================

A bulk delete of two hundred documents is the most consequential action in the
console. It writes an INGESTION_BATCH-scoped audit row carrying the action, the
count, the idempotency key, and the ids that were refused with their reasons.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api import deps
from app.core.config import settings
from app.core.principal import get_current_principal
from app.models.audit_log import AuditAction, AuditOutcome, AuditResourceType
from app.models.ingestion import (
    DocumentSchemaPreset,
    IngestionBatch,
    RetentionHold,
    WorkspaceSchemaPreset,
)
from app.schemas.ingestion import (
    BatchCreateRequest,
    BatchItemResponse,
    BatchResponse,
    BulkActionRequest,
    BulkActionResponse,
    PresetApplyRequest,
    PresetEnableRequest,
    PresetResponse,
    RetentionHoldRequest,
    RetentionHoldResponse,
    TagListResponse,
    UploadSessionCompleteResponse,
    UploadSessionCreateRequest,
    UploadSessionResponse,
)
from app.services import audit_service, document_intake_service, file_validation_service
from app.services.ingestion import (
    batch_service,
    bulk_service,
    preset_service,
    retention_service,
    upload_session_service,
)
from app.services.redaction.vocabulary import PROFILE_HIPAA_SAFE_HARBOR

logger = logging.getLogger("app.api.v1.ingestion")

session_router = APIRouter(tags=["Batch Ingestion"])
batch_router = APIRouter(tags=["Batch Ingestion"])
work_item_router = APIRouter(tags=["Work Items"])
preset_router = APIRouter(tags=["Document Presets"])

#: A part is read whole into memory before it is forwarded. The ceiling is the
#: driver's MAX_PART_SIZE; anything larger is refused before the body is read.
MAX_PART_BYTES = 64 * 1024 * 1024


def _fail(exc: Any, default_status: int = status.HTTP_400_BAD_REQUEST) -> HTTPException:
    code = getattr(exc, "code", None)
    if code in {"SESSION_NOT_FOUND", "BATCH_NOT_FOUND", "ITEM_NOT_FOUND", "PRESET_NOT_FOUND"}:
        return HTTPException(status.HTTP_404_NOT_FOUND, str(getattr(exc, "message", exc)))
    return HTTPException(default_status, str(getattr(exc, "message", exc)))


# ---------------------------------------------------------------------------
# Upload sessions
# ---------------------------------------------------------------------------


@session_router.post(
    "", response_model=UploadSessionResponse, status_code=status.HTTP_201_CREATED
)
async def create_upload_session(
    payload: UploadSessionCreateRequest,
    db: Session = Depends(deps.get_db),
    context: deps.TenantContext = Depends(deps.RequireWorkspaceContributor),
) -> UploadSessionResponse:
    try:
        session = upload_session_service.create(
            db,
            organization_id=context.organization_id,
            workspace_id=context.workspace_id,
            user_id=context.user_id,
            filename=payload.filename,
            mime_type=payload.mime_type,
            total_size=payload.total_size,
            expected_sha256=payload.sha256,
        )
    except upload_session_service.UploadSessionError as exc:
        raise _fail(exc)

    if payload.batch_item_id is not None:
        item = batch_service.assert_item_in_workspace(
            db, workspace_id=context.workspace_id, item_id=payload.batch_item_id
        )
        item.upload_session_id = session.id
        item.status = "UPLOADING"

    db.commit()
    db.refresh(session)
    return UploadSessionResponse.model_validate(session)


@session_router.get("/{session_id}", response_model=UploadSessionResponse)
async def get_upload_session(
    session_id: uuid.UUID,
    db: Session = Depends(deps.get_db),
    context: deps.TenantContext = Depends(deps.RequireWorkspaceContributor),
) -> UploadSessionResponse:
    """Resumption. The browser asks what the server holds; it never guesses."""
    try:
        session = upload_session_service.resume(
            db, workspace_id=context.workspace_id, session_id=session_id
        )
    except upload_session_service.UploadSessionError as exc:
        raise _fail(exc)
    return UploadSessionResponse.model_validate(session)


@session_router.put("/{session_id}/parts/{part_number}", response_model=UploadSessionResponse)
async def put_part(
    session_id: uuid.UUID,
    part_number: int,
    request: Request,
    db: Session = Depends(deps.get_db),
    context: deps.TenantContext = Depends(deps.RequireWorkspaceContributor),
) -> UploadSessionResponse:
    if part_number < 1 or part_number > 10_000:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "Part number must be between 1 and 10000."
        )

    declared = request.headers.get("content-length")
    if declared and int(declared) > MAX_PART_BYTES:
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            f"A part may not exceed {MAX_PART_BYTES} bytes.",
        )

    data = await request.body()
    if len(data) > MAX_PART_BYTES:
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            f"A part may not exceed {MAX_PART_BYTES} bytes.",
        )

    try:
        session = upload_session_service.receive_part(
            db,
            workspace_id=context.workspace_id,
            session_id=session_id,
            part_number=part_number,
            data=data,
        )
    except upload_session_service.UploadSessionError as exc:
        db.rollback()
        raise _fail(exc)

    db.commit()
    db.refresh(session)
    return UploadSessionResponse.model_validate(session)


@session_router.post(
    "/{session_id}/complete", response_model=UploadSessionCompleteResponse
)
async def complete_upload_session(
    session_id: uuid.UUID,
    db: Session = Depends(deps.get_db),
    context: deps.TenantContext = Depends(deps.RequireWorkspaceContributor),
) -> UploadSessionCompleteResponse:
    """Assemble, verify the sha256, then hand off to the ordinary intake path.

    The assembled bytes go back through `validate_spooled` exactly as a
    single-file upload does. A resumable upload is a transport, not a way
    around magic-byte checking, page probing or metadata scrubbing.
    """
    import io

    try:
        session, payload = upload_session_service.complete(
            db, workspace_id=context.workspace_id, session_id=session_id
        )
    except upload_session_service.UploadSessionError as exc:
        db.commit()  # the service may have recorded ABORTED; keep that
        raise _fail(exc)

    spool = io.BytesIO(payload)
    try:
        validated = file_validation_service.validate_spooled(
            spool,
            len(payload),
            declared_mime=session.mime_type,
            original_filename=session.filename,
            # ARCH40-S2:d13-session-enforcement (hardening tier 3). Upload
            # sessions obey the workspace file-type setting, which can only
            # narrow the platform list. This sentinel marks the file as
            # superseded for apply_arch38.py's idempotency check.
            allowed_mimes=file_validation_service.workspace_allowed_mimes(
                db, context.workspace_id, settings.ALLOWED_MIME_TYPES
            ),
            max_pages=settings.MAX_DOCUMENT_PAGES,
            scrub_metadata=settings.SCRUB_UPLOAD_METADATA,
        )
    except file_validation_service.FileValidationError as exc:
        quarantine_key = document_intake_service.quarantine(
            spool,
            organization_id=context.organization_id,
            error=exc,
            original_filename=session.filename,
        )
        document_intake_service.record_rejection(
            db,
            organization_id=context.organization_id,
            workspace_id=context.workspace_id,
            error=exc,
            original_filename=session.filename,
            quarantine_key=quarantine_key,
        )
        db.commit()
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))

    with validated:
        result = document_intake_service.ingest_validated(
            db,
            validated,
            organization_id=context.organization_id,
            workspace_id=context.workspace_id,
            uploader_id=context.user_id,
            enqueue_extraction=True,
        )

    batch_id: Optional[uuid.UUID] = None
    from app.models.ingestion import IngestionBatchItem

    item = db.execute(
        select(IngestionBatchItem).where(
            IngestionBatchItem.upload_session_id == session.id,
            IngestionBatchItem.workspace_id == context.workspace_id,
        )
    ).scalar_one_or_none()
    if item is not None:
        batch_id = item.batch_id
        batch_service.record_success(db, item=item, work_item_id=result.work_item.id)
        batch = batch_service.get_batch(
            db, workspace_id=context.workspace_id, batch_id=item.batch_id
        )
        batch_service.finalize_if_done(
            db, batch=batch, organization_id=context.organization_id
        )

    db.commit()
    return UploadSessionCompleteResponse(
        session_id=session.id,
        work_item_id=result.work_item.id,
        batch_id=batch_id,
    )


@session_router.delete("/{session_id}", status_code=status.HTTP_204_NO_CONTENT, response_class=Response)
async def abort_upload_session(
    session_id: uuid.UUID,
    db: Session = Depends(deps.get_db),
    context: deps.TenantContext = Depends(deps.RequireWorkspaceContributor),
) -> Response:
    try:
        upload_session_service.abort(
            db, workspace_id=context.workspace_id, session_id=session_id
        )
    except upload_session_service.UploadSessionError as exc:
        raise _fail(exc)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# Batches
# ---------------------------------------------------------------------------


def _batch_response(db: Session, batch: IngestionBatch) -> BatchResponse:
    items = batch_service.list_items(
        db, workspace_id=batch.workspace_id, batch_id=batch.id
    )
    payload = BatchResponse.model_validate(batch)
    payload.items = [BatchItemResponse.model_validate(item) for item in items]
    return payload


@batch_router.post("", response_model=BatchResponse, status_code=status.HTTP_201_CREATED)
async def create_batch(
    payload: BatchCreateRequest,
    db: Session = Depends(deps.get_db),
    context: deps.TenantContext = Depends(deps.RequireWorkspaceContributor),
) -> BatchResponse:
    try:
        batch = batch_service.create_batch(
            db,
            workspace_id=context.workspace_id,
            user_id=context.user_id,
            source=payload.source,
            files=[file.model_dump() for file in payload.files],
        )
    except batch_service.BatchError as exc:
        db.rollback()
        raise _fail(exc)

    audit_service.record(
        db,
        organization_id=context.organization_id,
        workspace_id=context.workspace_id,
        principal=get_current_principal(),
        resource_type=AuditResourceType.INGESTION_BATCH,
        resource_id=batch.id,
        action=AuditAction.CREATED,
        outcome=AuditOutcome.ALLOWED,
        details={"source": batch.source, "total_items": batch.total_items},
    )
    db.commit()
    db.refresh(batch)
    return _batch_response(db, batch)


@batch_router.get("/{batch_id}", response_model=BatchResponse)
async def get_batch(
    batch_id: uuid.UUID,
    db: Session = Depends(deps.get_db),
    context: deps.TenantContext = Depends(deps.RequireWorkspaceViewer),
) -> BatchResponse:
    try:
        batch = batch_service.get_batch(
            db, workspace_id=context.workspace_id, batch_id=batch_id
        )
    except batch_service.BatchError as exc:
        raise _fail(exc)
    return _batch_response(db, batch)


@batch_router.get("", response_model=list[BatchResponse])
async def list_batches(
    db: Session = Depends(deps.get_db),
    context: deps.TenantContext = Depends(deps.RequireWorkspaceViewer),
    limit: int = 20,
) -> list[BatchResponse]:
    rows = list(
        db.execute(
            select(IngestionBatch)
            .where(IngestionBatch.workspace_id == context.workspace_id)
            .order_by(IngestionBatch.created_at.desc())
            .limit(min(max(limit, 1), 100))
        ).scalars()
    )
    return [_batch_response(db, row) for row in rows]


# ---------------------------------------------------------------------------
# Bulk actions and tags
# ---------------------------------------------------------------------------


@work_item_router.post("/bulk", response_model=BulkActionResponse)
async def bulk_action(
    payload: BulkActionRequest,
    db: Session = Depends(deps.get_db),
    context: deps.TenantContext = Depends(deps.RequireWorkspaceContributor),
) -> BulkActionResponse:
    try:
        outcome = bulk_service.run(
            db,
            organization_id=context.organization_id,
            workspace_id=context.workspace_id,
            action=payload.action,
            ids=payload.ids,
            tags=payload.tags,
            export_format=payload.export_format,
            user_id=context.user_id,
        )
    except bulk_service.BulkError as exc:
        db.rollback()
        raise _fail(exc)

    audit_service.record(
        db,
        organization_id=context.organization_id,
        workspace_id=context.workspace_id,
        principal=get_current_principal(),
        resource_type=AuditResourceType.INGESTION_BATCH,
        action=(
            AuditAction.DELETED
            if payload.action == "delete"
            else AuditAction.EXPORTED
            if payload.action == "export"
            else AuditAction.UPDATED
        ),
        outcome=AuditOutcome.ALLOWED,
        details={
            "bulk_action": payload.action,
            "idempotency_key": payload.idempotency_key,
            "requested": len(payload.ids),
            "succeeded": outcome["succeeded"],
            "refused": outcome["refused"],
            "refusals": [
                {"id": r["work_item_id"], "code": r.get("code")}
                for r in outcome["results"]
                if r.get("outcome") == "refused"
            ][:50],
        },
    )
    db.commit()

    export = outcome.get("export") or {}
    return BulkActionResponse(
        action=outcome["action"],
        succeeded=outcome["succeeded"],
        refused=outcome["refused"],
        results=outcome["results"],
        export_body=export.get("body"),
        export_mime_type=export.get("mime_type"),
    )


@work_item_router.get("/tags", response_model=TagListResponse)
async def list_tags(
    db: Session = Depends(deps.get_db),
    context: deps.TenantContext = Depends(deps.RequireWorkspaceViewer),
) -> TagListResponse:
    return TagListResponse(
        tags=bulk_service.workspace_tags(db, workspace_id=context.workspace_id)
    )


# ---------------------------------------------------------------------------
# Presets
# ---------------------------------------------------------------------------


def _preset_response(
    preset: DocumentSchemaPreset, applied: Optional[WorkspaceSchemaPreset]
) -> PresetResponse:
    return PresetResponse(
        id=preset.id,
        organization_id=preset.organization_id,
        industry=preset.industry,
        document_type=preset.document_type,
        version=preset.version,
        label=preset.label,
        description=preset.description,
        schema_fields=preset.schema,
        assertions=list(preset.assertions or []),
        classifier_hints=list(preset.classifier_hints or []),
        redaction_profile=preset.redaction_profile,
        is_platform=preset.organization_id is None,
        applied=applied is not None,
        enabled=bool(applied and applied.enabled),
        safe_harbor_notice=(
            preset_service.SAFE_HARBOR_NOTICE
            if preset.redaction_profile == PROFILE_HIPAA_SAFE_HARBOR
            else None
        ),
    )


@preset_router.get("", response_model=list[PresetResponse])
async def list_presets(
    db: Session = Depends(deps.get_db),
    context: deps.TenantContext = Depends(deps.RequireWorkspaceViewer),
) -> list[PresetResponse]:
    presets = preset_service.visible_presets(
        db, organization_id=context.organization_id
    )
    applied = {
        row.preset_id: row
        for row in preset_service.applied_presets(
            db, workspace_id=context.workspace_id
        )
    }
    return [_preset_response(preset, applied.get(preset.id)) for preset in presets]


@preset_router.post("/apply", response_model=PresetResponse)
async def apply_preset(
    payload: PresetApplyRequest,
    db: Session = Depends(deps.get_db),
    context: deps.TenantContext = Depends(deps.RequireWorkspaceAdmin),
) -> PresetResponse:
    try:
        applied = preset_service.apply_to_workspace(
            db,
            organization_id=context.organization_id,
            workspace_id=context.workspace_id,
            preset_id=payload.preset_id,
            user_id=context.user_id,
        )
        preset = preset_service.get_preset(
            db, organization_id=context.organization_id, preset_id=payload.preset_id
        )
    except preset_service.PresetError as exc:
        db.rollback()
        raise _fail(exc)

    audit_service.record(
        db,
        organization_id=context.organization_id,
        workspace_id=context.workspace_id,
        principal=get_current_principal(),
        resource_type=AuditResourceType.DOCUMENT_SCHEMA_PRESET,
        resource_id=preset.id,
        action=AuditAction.CREATED,
        outcome=AuditOutcome.ALLOWED,
        details={"document_type": preset.document_type, "enabled": False},
    )
    db.commit()
    return _preset_response(preset, applied)


@preset_router.post("/{preset_id}/enabled", response_model=PresetResponse)
async def set_preset_enabled(
    preset_id: uuid.UUID,
    payload: PresetEnableRequest,
    db: Session = Depends(deps.get_db),
    context: deps.TenantContext = Depends(deps.RequireWorkspaceAdmin),
) -> PresetResponse:
    try:
        applied = preset_service.set_enabled(
            db,
            workspace_id=context.workspace_id,
            preset_id=preset_id,
            enabled=payload.enabled,
        )
        preset = preset_service.get_preset(
            db, organization_id=context.organization_id, preset_id=preset_id
        )
    except preset_service.PresetError as exc:
        db.rollback()
        raise _fail(exc)

    audit_service.record(
        db,
        organization_id=context.organization_id,
        workspace_id=context.workspace_id,
        principal=get_current_principal(),
        resource_type=AuditResourceType.DOCUMENT_SCHEMA_PRESET,
        resource_id=preset.id,
        action=AuditAction.ENABLED if payload.enabled else AuditAction.DISABLED,
        outcome=AuditOutcome.ALLOWED,
        details={"document_type": preset.document_type},
    )
    db.commit()
    return _preset_response(preset, applied)


# ---------------------------------------------------------------------------
# Retention holds
# ---------------------------------------------------------------------------


@work_item_router.get("/retention-holds", response_model=list[RetentionHoldResponse])
async def list_holds(
    db: Session = Depends(deps.get_db),
    context: deps.TenantContext = Depends(deps.RequireWorkspaceViewer),
) -> list[RetentionHoldResponse]:
    rows = list(
        db.execute(
            select(RetentionHold).where(
                RetentionHold.organization_id == context.organization_id,
                RetentionHold.released_at.is_(None),
            )
        ).scalars()
    )
    return [RetentionHoldResponse.model_validate(row) for row in rows]


@work_item_router.post(
    "/retention-holds",
    response_model=RetentionHoldResponse,
    status_code=status.HTTP_201_CREATED,
)
async def place_hold(
    payload: RetentionHoldRequest,
    db: Session = Depends(deps.get_db),
    context: deps.TenantContext = Depends(deps.RequireWorkspaceAdmin),
) -> RetentionHoldResponse:
    # A hold may only cover this workspace. Naming another workspace's id would
    # place a hold a tenant admin cannot see or release from here.
    workspace_id = payload.workspace_id or (
        context.workspace_id if payload.work_item_id is None else None
    )
    if workspace_id is not None and workspace_id != context.workspace_id:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, "Workspace not found."
        )
    if payload.work_item_id is not None:
        from app import crud

        target = crud.get_work_item(
            db,
            workspace_id=context.workspace_id,
            work_item_id=payload.work_item_id,
        )
        if target is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Document not found.")

    hold = retention_service.place_hold(
        db,
        organization_id=context.organization_id,
        workspace_id=workspace_id,
        work_item_id=payload.work_item_id,
        reason=payload.reason,
        reference=payload.reference,
        user_id=context.user_id,
    )
    audit_service.record(
        db,
        organization_id=context.organization_id,
        workspace_id=context.workspace_id,
        principal=get_current_principal(),
        resource_type=AuditResourceType.RETENTION_HOLD,
        resource_id=hold.id,
        action=AuditAction.CREATED,
        outcome=AuditOutcome.ALLOWED,
        details={"reason": hold.reason, "reference": hold.reference},
    )
    db.commit()
    db.refresh(hold)
    return RetentionHoldResponse.model_validate(hold)


@work_item_router.delete(
    "/retention-holds/{hold_id}", response_model=RetentionHoldResponse
)
async def release_hold(
    hold_id: uuid.UUID,
    db: Session = Depends(deps.get_db),
    context: deps.TenantContext = Depends(deps.RequireWorkspaceAdmin),
) -> RetentionHoldResponse:
    hold = retention_service.release_hold(
        db,
        organization_id=context.organization_id,
        hold_id=hold_id,
        user_id=context.user_id,
    )
    if hold is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Hold not found.")
    audit_service.record(
        db,
        organization_id=context.organization_id,
        workspace_id=context.workspace_id,
        principal=get_current_principal(),
        resource_type=AuditResourceType.RETENTION_HOLD,
        resource_id=hold.id,
        action=AuditAction.REVOKED,
        outcome=AuditOutcome.ALLOWED,
        details={"reason": hold.reason},
    )
    db.commit()
    db.refresh(hold)
    return RetentionHoldResponse.model_validate(hold)

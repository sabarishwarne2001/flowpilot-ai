"""ARCH-10 Step 5 — document intake."""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import Any, Optional

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.principal import Principal, get_current_principal
from app.core.storage import (
    StorageError,
    StorageNamespace,
    get_storage_driver,
    tenant_key,
)
from app.models.audit_log import AuditAction, AuditOutcome, AuditResourceType
from app.models.job import Job
from app.models.uploaded_file import UploadedFile
from app.models.work_item import WorkItem
from app.services import audit_service, job_service
from app.services.file_validation_service import (
    FileValidationError,
    RejectionReason,
    ValidatedUpload,
)

logger = logging.getLogger("app.services.document_intake")

OCR_JOB_TYPE = "document.extract"

_SUFFIX_BY_MIME: dict[str, str] = {
    "application/pdf": "pdf",
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/tiff": "tiff",
    "image/webp": "webp",
    "image/gif": "gif",
    "image/bmp": "bmp",
}


@dataclass(frozen=True)
class IntakeResult:
    work_item: WorkItem
    uploaded_file: UploadedFile
    job: Job
    storage_key: str
    page_count: Optional[int]
    scrubbed: bool


def _suffix_for(mime_type: str) -> Optional[str]:
    return _SUFFIX_BY_MIME.get(mime_type)


#: The longest name a document keeps (work_items / uploaded_files original_filename).
MAX_FILENAME_LENGTH = 255


def fit_filename(name: str, limit: int = MAX_FILENAME_LENGTH) -> str:
    """`name` cut to `limit` characters, keeping its extension (F-159).

    A plain slice of a long name dropped the ".pdf", so the document downloaded without one.
    """
    if len(name) <= limit:
        return name
    stem, dot, extension = name.rpartition(".")
    if dot and stem and 0 < len(extension) <= 16:
        return f"{stem[: limit - len(extension) - 1]}.{extension}"
    return name[:limit]


def duplicate_detection_enabled(db: Session, *, workspace_id: uuid.UUID) -> bool:
    """The workspace's "Duplicate detection" setting; on when the workspace never saved one."""
    from sqlalchemy import select

    from app.models.document_settings import DocumentSettings

    value = db.execute(
        select(DocumentSettings.duplicate_detection).where(DocumentSettings.workspace_id == workspace_id)
    ).scalar_one_or_none()
    return True if value is None else bool(value)


def find_duplicate(db: Session, *, workspace_id: uuid.UUID, checksum_sha256: str) -> Optional[WorkItem]:
    """The earliest document in the workspace whose stored file has these exact bytes (F-158).

    Takes a transaction-scoped advisory lock on (workspace, checksum) first, so two copies uploaded
    at the same moment are serialised: the second waits for the first to commit and then sees it.
    """
    from sqlalchemy import func, select, text

    db.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
        {"key": f"work-item-duplicate:{workspace_id}:{checksum_sha256}"},
    )
    return db.execute(
        select(WorkItem)
        .join(UploadedFile, UploadedFile.id == WorkItem.uploaded_file_id)
        .where(
            WorkItem.workspace_id == workspace_id,
            UploadedFile.checksum_sha256 == func.lower(checksum_sha256),
            UploadedFile.deleted_at.is_(None),
        )
        .order_by(WorkItem.created_at.asc(), WorkItem.id.asc())
        .limit(1)
    ).scalar_one_or_none()


def quarantine(
    validated_bytes: Any,
    *,
    organization_id: uuid.UUID,
    error: FileValidationError,
    original_filename: str,
) -> Optional[str]:
    if not error.should_quarantine:
        return None
    try:
        driver = get_storage_driver()
        key = tenant_key(
            organization_id=organization_id,
            namespace=StorageNamespace.QUARANTINE,
            file_id=uuid.uuid4(),
        )
        validated_bytes.seek(0)
        driver.put_stream(key, validated_bytes, "application/octet-stream")
        logger.warning(
            "intake.quarantined",
            extra={
                "organization_id": str(organization_id),
                "key": key,
                "reason": error.reason.value,
                "original_filename": original_filename,
            },
        )
        return key
    except Exception as exc:
        logger.exception("intake.quarantine_failed", extra={"error": str(exc)})
        return None


def record_rejection(
    db: Session,
    *,
    organization_id: uuid.UUID,
    workspace_id: uuid.UUID,
    error: FileValidationError,
    original_filename: str,
    quarantine_key: Optional[str] = None,
    principal: Optional[Principal] = None,
) -> None:
    audit_service.record_independently(
        db,
        organization_id=organization_id,
        workspace_id=workspace_id,
        principal=principal or get_current_principal(),
        resource_type=AuditResourceType.UPLOADED_FILE,
        action=AuditAction.CREATED,
        outcome=AuditOutcome.DENIED,
        details={
            "original_filename": original_filename[:255],
            "quarantine_key": quarantine_key,
            **error.audit_details(),
        },
    )


def ingest_validated(
    db: Session,
    validated: ValidatedUpload,
    *,
    organization_id: uuid.UUID,
    workspace_id: uuid.UUID,
    uploader_id: Optional[uuid.UUID],
    principal: Optional[Principal] = None,
    enqueue_extraction: bool = True,
    meter_upload: bool = True,
) -> IntakeResult:
    """Store a validated file and create its document, charged to the plan.

    Campaign session 1: every upload path (browser, API key, multipart sessions,
    batch archives, public document-request links) comes through here, so this is
    where a document is charged (`plan_admission`): a cheap refusal before the
    bytes are stored, then the authoritative check and charge under the usage
    pool's lock in this transaction. `meter_upload=False` only for documents carved
    out of one already charged (a packet's split children).
    """
    from app.services import plan_admission

    if meter_upload:
        plan_admission.precheck_document(
            db,
            organization_id=organization_id,
            size_bytes=int(validated.size),
            page_count=validated.page_count,
        )

    driver = get_storage_driver()
    file_id = uuid.uuid4()
    key = tenant_key(
        organization_id=organization_id,
        namespace=StorageNamespace.DOCUMENTS,
        file_id=file_id,
        suffix=_suffix_for(validated.mime_type),
    )

    validated.handle.seek(0)
    stored = driver.put_stream(
        key,
        validated.handle,
        validated.mime_type,
        content_length=validated.size,
        checksum_sha256=validated.checksum_sha256,
    )

    try:
        if meter_upload:
            plan_admission.admit_document(
                db,
                organization_id=organization_id,
                workspace_id=workspace_id,
                size_bytes=int(stored.size),
                page_count=validated.page_count,
                principal=principal,
                idempotency_key=f"document.upload:{file_id}",
            )
        original_filename = fit_filename(validated.original_filename)
        duplicate_of: Optional[WorkItem] = None
        if duplicate_detection_enabled(db, workspace_id=workspace_id):
            duplicate_of = find_duplicate(
                db, workspace_id=workspace_id, checksum_sha256=stored.checksum_sha256
            )

        uploaded = UploadedFile(
            id=file_id,
            owner_id=uploader_id,
            organization_id=organization_id,
            workspace_id=workspace_id,
            file_path=stored.key,
            original_filename=original_filename,
            mime_type=validated.mime_type,
            file_size=stored.size,
            checksum_sha256=stored.checksum_sha256,
        )
        db.add(uploaded)
        db.flush([uploaded])

        work_item = WorkItem(
            original_filename=original_filename,
            stored_filename=stored.key[:255],
            file_type=validated.mime_type,
            file_size=stored.size,
            status="QUEUED",
            workspace_id=workspace_id,
            created_by_user_id=uploader_id,
            uploaded_file_id=uploaded.id,
            page_count=validated.page_count,
            duplicate_of_work_item_id=duplicate_of.id if duplicate_of else None,
        )
        db.add(work_item)
        db.flush([work_item])

        job: Optional[Job] = None
        if enqueue_extraction:
            job = job_service.enqueue(
                db,
                job_type=OCR_JOB_TYPE,
                payload={
                    "work_item_id": str(work_item.id),
                    "uploaded_file_id": str(uploaded.id),
                    "storage_key": stored.key,
                    "mime_type": validated.mime_type,
                    "page_count": validated.page_count,
                },
                organization_id=organization_id,
                idempotency_key=f"{OCR_JOB_TYPE}:{work_item.id}",
                max_attempts=settings.OCR_JOB_MAX_ATTEMPTS,
            )

        audit_service.record(
            db,
            organization_id=organization_id,
            workspace_id=workspace_id,
            principal=principal or get_current_principal(),
            resource_type=AuditResourceType.UPLOADED_FILE,
            resource_id=uploaded.id,
            action=AuditAction.CREATED,
            outcome=AuditOutcome.ALLOWED,
            details={
                "storage_key": stored.key,
                "mime_type": validated.mime_type,
                "size_bytes": stored.size,
                "checksum_sha256": stored.checksum_sha256,
                "page_count": validated.page_count,
                "metadata_scrubbed": validated.scrubbed,
                "multipart": stored.multipart,
                "work_item_id": str(work_item.id),
                "job_id": str(job.id) if job else None,
                "notes": validated.notes or None,
                "duplicate_of_work_item_id": str(duplicate_of.id) if duplicate_of else None,
            },
        )

        # ARCH37-S1:work-item-created. No public event has ever been emitted
        # for a new document, so this trigger is native.
        from app.services import outbox_service

        outbox_service.emit_trigger(
            db,
            organization_id=organization_id,
            workspace_id=workspace_id,
            event_type="trigger.work_item.created",
            resource_id=work_item.id,
            payload={
                "work_item_id": str(work_item.id),
                "original_filename": work_item.original_filename,
                "mime_type": validated.mime_type,
                "size_bytes": int(stored.size),
                "page_count": validated.page_count,
            },
            idempotency_key=f"trigger.work_item.created:{work_item.id}",
        )

    except Exception:
        try:
            driver.delete(stored.key)
        except StorageError:
            logger.exception(
                "intake.orphan_cleanup_failed", extra={"key": stored.key}
            )
        raise

    logger.info(
        "intake.accepted",
        extra={
            "organization_id": str(organization_id),
            "workspace_id": str(workspace_id),
            "work_item_id": str(work_item.id),
            "key": stored.key,
            "size": stored.size,
            "page_count": validated.page_count,
        },
    )

    return IntakeResult(
        work_item=work_item,
        uploaded_file=uploaded,
        job=job,
        storage_key=stored.key,
        page_count=validated.page_count,
        scrubbed=validated.scrubbed,
    )

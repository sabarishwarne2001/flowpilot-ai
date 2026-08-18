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
) -> IntakeResult:
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
        uploaded = UploadedFile(
            id=file_id,
            owner_id=uploader_id,
            organization_id=organization_id,
            workspace_id=workspace_id,
            file_path=stored.key,
            original_filename=validated.original_filename[:255],
            mime_type=validated.mime_type,
            file_size=stored.size,
            checksum_sha256=stored.checksum_sha256,
        )
        db.add(uploaded)
        db.flush([uploaded])

        work_item = WorkItem(
            original_filename=validated.original_filename[:255],
            stored_filename=stored.key[:255],
            file_type=validated.mime_type,
            file_size=stored.size,
            status="QUEUED",
            workspace_id=workspace_id,
            created_by_user_id=uploader_id,
            uploaded_file_id=uploaded.id,
            page_count=validated.page_count,
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
            },
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
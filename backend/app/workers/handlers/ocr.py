"""ARCH-10 Step 6 — the `document.extract` job handler."""

from __future__ import annotations

import logging
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.exceptions import SpendLimitExceededError
from app.core.storage import (
    ObjectNotFoundError,
    StorageError,
    assert_key_belongs_to,
    get_storage_driver,
)
from app.models.audit_log import AuditAction, AuditOutcome, AuditResourceType
from app.models.work_item import WorkItem
from app.models.workspace import Workspace
from app.services import audit_service
from app.services import spend_control_service as spend
from app.services.ocr.base import (
    OCRError,
    OCRResult,
    OCRUnavailableError,
    OCRUnsupportedError,
)

logger = logging.getLogger("app.workers.handlers.ocr")

JOB_TYPE = "document.extract"
USAGE_EVENT_TYPE = "ocr.page"

STATUS_PROCESSING = "PROCESSING"
STATUS_COMPLETED = "COMPLETED"
STATUS_FAILED = "FAILED"


class Outcome:
    COMPLETED = "COMPLETED"
    PERMANENT_FAILURE = "PERMANENT_FAILURE"
    QUOTA_BLOCKED = "QUOTA_BLOCKED"
    SKIPPED = "SKIPPED"


@dataclass(frozen=True)
class _Target:
    work_item_id: uuid.UUID
    organization_id: uuid.UUID
    workspace_id: uuid.UUID
    storage_key: str
    mime_type: str
    estimated_pages: int
    already_done: bool


def _resolve(db: Session, payload: dict[str, Any]) -> Optional[_Target]:
    raw_id = payload.get("work_item_id")
    if not raw_id:
        raise ValueError("document.extract payload is missing work_item_id")

    work_item_id = uuid.UUID(str(raw_id))
    row = db.execute(
        select(WorkItem, Workspace.organization_id)
        .join(Workspace, Workspace.id == WorkItem.workspace_id)
        .where(WorkItem.id == work_item_id)
    ).first()

    if row is None:
        logger.info("ocr.work_item_gone", extra={"work_item_id": str(work_item_id)})
        return None

    work_item, organization_id = row
    storage_key = payload.get("storage_key") or work_item.stored_filename

    assert_key_belongs_to(storage_key, organization_id)

    estimated = int(
        payload.get("page_count") or getattr(work_item, "page_count", None) or 1
    )

    return _Target(
        work_item_id=work_item.id,
        organization_id=organization_id,
        workspace_id=work_item.workspace_id,
        storage_key=storage_key,
        mime_type=payload.get("mime_type") or work_item.file_type,
        estimated_pages=max(1, estimated),
        already_done=work_item.status == STATUS_COMPLETED,
    )


def _mark_status(
    db: Session, work_item_id: uuid.UUID, status: str, **fields: Any
) -> None:
    work_item = db.execute(
        select(WorkItem).where(WorkItem.id == work_item_id)
    ).scalar_one_or_none()
    if work_item is None:
        return
    work_item.status = status
    for name, value in fields.items():
        if hasattr(work_item, name):
            setattr(work_item, name, value)
    db.flush([work_item])


def _download_and_extract(target: _Target) -> OCRResult:
    from app.services.ocr.paddle import get_provider

    driver = get_storage_driver()
    provider = get_provider()

    if not provider.supports(target.mime_type):
        raise OCRUnsupportedError(
            f"{provider.name} cannot process {target.mime_type!r}."
        )

    suffix = Path(target.storage_key).suffix or ".bin"
    handle = tempfile.NamedTemporaryFile(
        prefix="fp-doc-", suffix=suffix, delete=False
    )
    temp_path = Path(handle.name)
    try:
        try:
            driver.download_to(target.storage_key, handle)
        except ObjectNotFoundError as exc:
            raise OCRUnsupportedError(
                f"Object {target.storage_key!r} is missing from storage."
            ) from exc
        handle.close()

        return provider.extract(
            temp_path,
            mime_type=target.mime_type,
            language=settings.OCR_LANGUAGE,
            max_pages=settings.OCR_MAX_PAGES_PER_DOCUMENT,
        )
    finally:
        try:
            handle.close()
        except Exception:
            pass
        temp_path.unlink(missing_ok=True)


def _guard_before_extraction(target: _Target) -> None:
    if not settings.OCR_GUARD_BEFORE_EXTRACTION:
        return

    from app.db.session import SessionLocal
    from app.services.ocr.paddle import get_provider

    provider = get_provider()
    with SessionLocal() as db:
        spend.ensure_within_limits(
            db,
            organization_id=target.organization_id,
            event_type=USAGE_EVENT_TYPE,
            quantity=target.estimated_pages,
            cost_micros=provider.estimated_cost_micros(target.estimated_pages),
            workspace_id=target.workspace_id,
        )
        db.commit()


def _persist(db: Session, target: _Target, result: OCRResult) -> dict[str, Any]:
    from app.services.ocr.paddle import get_provider

    provider = get_provider()
    billable = result.billable_pages

    if billable <= 0:
        _mark_status(db, target.work_item_id, STATUS_COMPLETED)
        _store_extraction(db, target, result)
        return {
            "outcome": Outcome.COMPLETED,
            "billable_pages": 0,
            **result.summary(),
        }

    with spend.guard_usage(
        db,
        organization_id=target.organization_id,
        event_type=USAGE_EVENT_TYPE,
        estimated_quantity=billable,
        estimated_cost_micros=provider.estimated_cost_micros(billable),
        workspace_id=target.workspace_id,
        resource_type="WORK_ITEM",
        resource_id=target.work_item_id,
        idempotency_key=f"ocr:{target.work_item_id}",
    ) as guard:
        guard.record(
            quantity=billable,
            cost_micros=provider.estimated_cost_micros(billable),
            provider=provider.name,
            details={
                "pages_total": result.page_count,
                "pages_text_layer": result.page_count - billable,
                "mean_confidence": result.mean_confidence,
                "model": result.model,
                "duration_seconds": round(result.duration_seconds, 3),
            },
        )

    _store_extraction(db, target, result)
    _mark_status(db, target.work_item_id, STATUS_COMPLETED)

    audit_service.record(
        db,
        organization_id=target.organization_id,
        workspace_id=target.workspace_id,
        resource_type=AuditResourceType.UPLOADED_FILE,
        resource_id=target.work_item_id,
        action=AuditAction.UPDATED,
        outcome=AuditOutcome.ALLOWED,
        details={"extraction": result.summary()},
    )

    return {"outcome": Outcome.COMPLETED, "billable_pages": billable, **result.summary()}


def _store_extraction(db: Session, target: _Target, result: OCRResult) -> None:
    work_item = db.execute(
        select(WorkItem).where(WorkItem.id == target.work_item_id)
    ).scalar_one_or_none()
    if work_item is None:
        return

    if hasattr(work_item, "extracted_text"):
        work_item.extracted_text = result.text
    if hasattr(work_item, "page_count"):
        work_item.page_count = result.page_count
    if hasattr(work_item, "extraction_metadata"):
        work_item.extraction_metadata = {
            **result.summary(),
            "pages": [page.as_dict() for page in result.pages],
        }
    db.flush([work_item])


def handle_document_extract(payload: dict[str, Any]) -> dict[str, Any]:
    from app.db.session import SessionLocal

    with SessionLocal() as db:
        target = _resolve(db, payload)
        if target is None:
            db.commit()
            return {"outcome": Outcome.SKIPPED, "reason": "work item no longer exists"}
        if target.already_done:
            db.commit()
            return {"outcome": Outcome.SKIPPED, "reason": "already COMPLETED"}
        _mark_status(db, target.work_item_id, STATUS_PROCESSING)
        db.commit()

    try:
        _guard_before_extraction(target)
    except SpendLimitExceededError as exc:
        return _block_on_quota(target, exc)

    try:
        result = _download_and_extract(target)
    except (OCRUnsupportedError, ValueError) as exc:
        return _permanent_failure(target, exc)
    except OCRUnavailableError:
        logger.warning(
            "ocr.engine_unavailable",
            extra={"work_item_id": str(target.work_item_id)},
        )
        raise
    except StorageError:
        logger.warning(
            "ocr.storage_error", extra={"work_item_id": str(target.work_item_id)}
        )
        raise
    except OCRError as exc:
        logger.warning(
            "ocr.extraction_error",
            extra={"work_item_id": str(target.work_item_id), "error": str(exc)},
        )
        raise

    with SessionLocal() as db:
        try:
            summary = _persist(db, target, result)
            db.commit()
            return summary
        except SpendLimitExceededError as exc:
            db.rollback()
            return _block_on_quota(target, exc)


def _permanent_failure(target: _Target, exc: Exception) -> dict[str, Any]:
    from app.db.session import SessionLocal

    logger.warning(
        "ocr.permanent_failure",
        extra={
            "work_item_id": str(target.work_item_id),
            "error": f"{type(exc).__name__}: {exc}",
        },
    )
    with SessionLocal() as db:
        _mark_status(db, target.work_item_id, STATUS_FAILED)
        audit_service.record(
            db,
            organization_id=target.organization_id,
            workspace_id=target.workspace_id,
            resource_type=AuditResourceType.UPLOADED_FILE,
            resource_id=target.work_item_id,
            action=AuditAction.UPDATED,
            outcome=AuditOutcome.DENIED,
            details={
                "stage": "extraction",
                "error": f"{type(exc).__name__}: {exc}",
                "permanent": True,
            },
        )
        db.commit()
    return {
        "outcome": Outcome.PERMANENT_FAILURE,
        "error": f"{type(exc).__name__}: {exc}",
    }


def _block_on_quota(
    target: _Target, exc: SpendLimitExceededError
) -> dict[str, Any]:
    from app.db.session import SessionLocal

    logger.warning(
        "ocr.quota_blocked",
        extra={
            "work_item_id": str(target.work_item_id),
            "organization_id": str(target.organization_id),
            "limit_key": exc.limit_key,
            "period": exc.period,
        },
    )
    with SessionLocal() as db:
        _mark_status(db, target.work_item_id, STATUS_FAILED)
        db.commit()
    return {
        "outcome": Outcome.QUOTA_BLOCKED,
        "limit_key": exc.limit_key,
        "period": exc.period,
        "dimension": exc.dimension,
        "resets_at": exc.resets_at.isoformat() if exc.resets_at else None,
        "retryable_after_reset": True,
    }
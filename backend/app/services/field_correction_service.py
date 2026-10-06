"""N-020 item 7 — correct an extracted field directly in the document viewer.

Until now an extracted value could only be corrected on a review-queue item,
and review items exist only where the verification agents disagreed. A person
looking at a finished document and seeing a wrong invoice number had no way to
fix it. This is that way, with four rules:

1. **The review queue keeps its document.** While a verification is open
   (PENDING / DISAGREED) the automation it holds back is waiting for the
   reviewer's values; a value written here would bypass that review. Such a
   document is refused with REVIEW_PENDING and corrected in the queue.
2. **A legal hold freezes the record** (F-108). Changing a held document's
   extracted values is altering evidence; refused with RETENTION_HOLD.
3. **Only plain values.** A field holding a table or a nested group (line
   items) is corrected in Tables; this edits text, numbers, booleans and
   empty values, under a field name the extraction could have produced.
4. **Every change is accounted for.** One `work_item_field_corrections` row per
   changed field (before, after, who, why), one audit row, and one
   `work_item.updated` event so webhooks and workflows see the new values.

Extraction memory learns from people's corrections (ARCH-41). Where the
document went through verification, the corrected value becomes that field's
reviewed value and the harvest runs exactly as after a review, so the layout's
memory learns the correction. A document never verified has no field rows to
anchor an exemplar to; its correction is recorded and audited only.
"""

from __future__ import annotations

import logging
import math
import re
import uuid
from dataclasses import dataclass, field
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.verification import (
    BLOCKING_STATUSES,
    RELEASING_STATUSES,
    DocumentVerification,
)
from app.models.work_item import WorkItem
from app.models.work_item_field_correction import WorkItemFieldCorrection

logger = logging.getLogger("app.services.field_correction_service")

#: A field name the extraction could have produced (top-level key of extracted_entities).
FIELD_PATH_PATTERN = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_ .\-]{0,99}$")
MAX_CORRECTIONS = 50
MAX_VALUE_CHARS = 2000
MAX_REASON_CHARS = 500
NOT_FINISHED = frozenset({"QUEUED", "PROCESSING"})


class FieldCorrectionError(Exception):
    def __init__(self, code: str, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


@dataclass
class CorrectionOutcome:
    work_item: WorkItem
    changed: list[WorkItemFieldCorrection] = field(default_factory=list)
    memory_fields: list[str] = field(default_factory=list)


def _is_scalar(value: Any) -> bool:
    if value is None or isinstance(value, (str, bool, int)):
        return True
    return isinstance(value, float) and math.isfinite(value)


def _same(left: Any, right: Any) -> bool:
    if isinstance(left, bool) or isinstance(right, bool):
        return left is right
    return left == right


def _clean(corrections: dict[str, Any]) -> dict[str, Any]:
    if not corrections:
        raise FieldCorrectionError("NO_CORRECTIONS", "Name at least one field to correct.", 422)
    if len(corrections) > MAX_CORRECTIONS:
        raise FieldCorrectionError(
            "TOO_MANY_CORRECTIONS", f"Correct at most {MAX_CORRECTIONS} fields at a time.", 422
        )
    cleaned: dict[str, Any] = {}
    for path, value in corrections.items():
        if not isinstance(path, str) or not FIELD_PATH_PATTERN.match(path):
            raise FieldCorrectionError("INVALID_FIELD", f"'{str(path)[:60]}' is not a field name.", 422)
        if not _is_scalar(value):
            raise FieldCorrectionError(
                "VALUE_NOT_PLAIN",
                f"'{path}' can only be set to text, a number, true/false or empty.",
                422,
            )
        if isinstance(value, str):
            value = value.strip()
            if "\x00" in value:
                raise FieldCorrectionError("INVALID_VALUE", f"'{path}' contains a NUL character.", 422)
            if len(value) > MAX_VALUE_CHARS:
                raise FieldCorrectionError(
                    "VALUE_TOO_LONG", f"'{path}' may be at most {MAX_VALUE_CHARS} characters.", 422
                )
            value = value or None
        cleaned[path] = value
    return cleaned


def _guard(db: Session, *, work_item: WorkItem, organization_id: uuid.UUID) -> None:
    status = str(work_item.status or "").upper()
    if status in NOT_FINISHED:
        raise FieldCorrectionError(
            "DOCUMENT_NOT_READY", "This document is still being processed. Try again when it has finished.", 409
        )
    if status == "FAILED":
        raise FieldCorrectionError(
            "DOCUMENT_FAILED", "Processing failed for this document. Reprocess it before correcting fields.", 409
        )

    from app.services.document_verification_service import blocking_verification

    if blocking_verification(db, work_item_id=work_item.id) is not None:
        raise FieldCorrectionError(
            "REVIEW_PENDING",
            "This document is waiting in the review queue. Correct it there, so the review and "
            "anything waiting on it use your value.",
            409,
        )

    from app.services.ingestion import retention_service

    holds = retention_service.active_holds(
        db, organization_id=organization_id, workspace_id=work_item.workspace_id, work_item_ids=[work_item.id]
    )
    hold = holds.get(work_item.id)
    if hold is not None:
        raise FieldCorrectionError(
            retention_service.HOLD_BLOCK_CODE,
            f"A legal hold freezes this document's record: {hold.reason}",
            409,
        )


def _teach_memory(
    db: Session, *, work_item: WorkItem, changed: dict[str, Any]
) -> list[str]:
    """Hand the correction to extraction memory through the document's verification, if it has one."""
    verification = db.execute(
        select(DocumentVerification)
        .where(
            DocumentVerification.work_item_id == work_item.id,
            DocumentVerification.status.in_(RELEASING_STATUSES),
        )
        .order_by(DocumentVerification.created_at.desc())
        .limit(1)
    ).scalar_one_or_none()
    if verification is None:
        return []
    rows = {row.field_path: row for row in verification.fields}
    taught = [path for path in changed if path in rows]
    if not taught:
        return []
    for path in taught:
        rows[path].resolved_value = changed[path]
    db.flush()

    from app.services.extraction_memory import harvest as memory_harvest

    memory_harvest.on_review_resolved(db, verification=verification, work_item=work_item)
    return taught


def correct_fields(
    db: Session,
    *,
    work_item: WorkItem,
    organization_id: uuid.UUID,
    user_id: uuid.UUID,
    corrections: dict[str, Any],
    reason: Optional[str] = None,
) -> CorrectionOutcome:
    cleaned = _clean(corrections)
    _guard(db, work_item=work_item, organization_id=organization_id)

    entities = dict(work_item.extracted_entities or {})
    for path in cleaned:
        if path in entities and not _is_scalar(entities[path]):
            raise FieldCorrectionError(
                "FIELD_NOT_PLAIN",
                f"'{path}' holds a table or a group of values. Correct it in Tables.",
                422,
            )

    note = (reason or "").strip()[:MAX_REASON_CHARS] or None
    outcome = CorrectionOutcome(work_item=work_item)
    changed: dict[str, Any] = {}
    for path, value in cleaned.items():
        before = entities.get(path)
        if path in entities and _same(before, value):
            continue
        if path not in entities and value is None:
            continue
        entities[path] = value
        changed[path] = value
        row = WorkItemFieldCorrection(
            id=uuid.uuid4(),
            workspace_id=work_item.workspace_id,
            work_item_id=work_item.id,
            field_path=path,
            previous_value=before,
            corrected_value=value,
            corrected_by_user_id=user_id,
            reason=note,
        )
        db.add(row)
        outcome.changed.append(row)

    if not changed:
        return outcome

    # A new dict, so the JSONB column is seen as changed.
    work_item.extracted_entities = entities
    db.flush()

    outcome.memory_fields = _teach_memory(db, work_item=work_item, changed=changed)

    from app.models.audit_log import AuditAction, AuditResourceType
    from app.services import audit_service, outbox_service

    audit_service.record(
        db,
        organization_id=organization_id,
        workspace_id=work_item.workspace_id,
        actor_id=user_id,
        resource_type=AuditResourceType.WORK_ITEM,
        resource_id=work_item.id,
        action=AuditAction.UPDATED,
        # Field names only: the values are document content, kept in the correction rows.
        details={"fields_corrected": sorted(changed), "source": "document_viewer",
                 "extraction_memory_fields": outcome.memory_fields},
    )
    outbox_service.emit(
        db,
        organization_id=organization_id,
        workspace_id=work_item.workspace_id,
        event_type="work_item.updated",
        resource_id=work_item.id,
        payload={"work_item_id": str(work_item.id), "fields_corrected": sorted(changed),
                 "source": "field_correction"},
    )
    logger.info(
        "work_item.fields_corrected",
        extra={"work_item_id": str(work_item.id), "fields": sorted(changed),
               "memory_fields": outcome.memory_fields},
    )
    return outcome


def editability(db: Session, *, work_item: WorkItem, organization_id: uuid.UUID) -> Optional[FieldCorrectionError]:
    """Why this document's fields cannot be corrected right now, or None when they can.

    The viewer asks before it offers an Edit button, so a person is told "it is in the review
    queue" or "a legal hold freezes it" up front instead of after typing a value.
    """
    try:
        _guard(db, work_item=work_item, organization_id=organization_id)
    except FieldCorrectionError as exc:
        return exc
    return None


def history(db: Session, *, work_item: WorkItem, limit: int = 200) -> list[WorkItemFieldCorrection]:
    return list(
        db.execute(
            select(WorkItemFieldCorrection)
            .where(
                WorkItemFieldCorrection.work_item_id == work_item.id,
                WorkItemFieldCorrection.workspace_id == work_item.workspace_id,
            )
            .order_by(WorkItemFieldCorrection.created_at.desc())
            .limit(limit)
        ).scalars()
    )


__all__ = [
    "CorrectionOutcome",
    "FieldCorrectionError",
    "correct_fields",
    "editability",
    "history",
]

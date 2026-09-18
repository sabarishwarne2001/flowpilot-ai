"""ARCH-38 — bulk actions over work items.

`POST /workspaces/{id}/work-items/bulk` takes
`{action, ids[], idempotency_key}`, runs as a job, and returns a result per
item. Nothing here is all-or-nothing: one document under a retention hold does
not stop the other 199 from being deleted, and the caller is told exactly which
one was refused and why.

WHY PER-ITEM RESULTS RATHER THAN A COUNT
========================================

"197 of 200 deleted" with no list is an answer nobody can act on. Each entry
carries the document id, an outcome, and for a refusal a stable code plus a
sentence, so the failures drawer in the console can say "3 documents are under
a retention hold" and name them.

TAG VALIDATION IS HERE AND IN THE DATABASE
==========================================

The `ck_work_item_tags_tag_format` CHECK is the backstop. `normalise_tag` is
the useful error: it lowercases and trims first, so "Q3 Review" becomes a
refusal that explains the rule rather than a 23514 from PostgreSQL. Removing
the CHECK is verify_arch38's mutant M4, and gate T2 dies on it -- the service
alone is not the control, because a future caller could write the row directly.
"""

from __future__ import annotations

import csv
import io
import json
import logging
import re
import uuid
from dataclasses import asdict, dataclass
from typing import Any, Iterable, Literal, Optional, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.ingestion import TAG_PATTERN, WorkItemTag
from app.models.work_item import WorkItem
from app.services.ingestion import retention_service

logger = logging.getLogger("app.services.ingestion.bulk")

BulkAction = Literal["delete", "reprocess", "export", "tag"]

BULK_ACTIONS: tuple[str, ...] = ("delete", "reprocess", "export", "tag")

#: One request may not name more than this many documents. Select-all-matching
#: in the console resolves to ids client-side and chunks at this size.
MAX_BULK_IDS = 500

_TAG_RE = re.compile(TAG_PATTERN)

MAX_TAGS_PER_ITEM = 24


@dataclass(frozen=True)
class ItemResult:
    work_item_id: str
    outcome: Literal["ok", "refused", "skipped"]
    code: Optional[str] = None
    detail: Optional[str] = None

    def as_dict(self) -> dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if v is not None}


class BulkError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def normalise_tag(raw: str) -> str:
    """Lowercase, trim, and refuse anything the CHECK would refuse."""
    candidate = (raw or "").strip().lower().replace(" ", "-")
    if not _TAG_RE.match(candidate):
        raise BulkError(
            "TAG_INVALID",
            f"{raw!r} is not a valid tag. Use lowercase letters, digits, "
            "hyphens and underscores, starting with a letter or digit, up to "
            "48 characters.",
        )
    return candidate


def _scoped_items(
    db: Session, *, workspace_id: uuid.UUID, ids: Sequence[uuid.UUID]
) -> list[WorkItem]:
    """Load only this workspace's documents.

    The workspace predicate is in the SQL. An id belonging to another tenant
    simply does not come back, and the caller reports it as not found -- the
    ARCH-14 §14.7 shape, where "yours but missing" and "someone else's" are
    indistinguishable.
    """
    if not ids:
        return []
    return list(
        db.execute(
            select(WorkItem).where(
                WorkItem.workspace_id == workspace_id,
                WorkItem.id.in_(list(ids)),
            )
        ).scalars()
    )


def _missing(ids: Sequence[uuid.UUID], found: Iterable[WorkItem]) -> list[ItemResult]:
    found_ids = {item.id for item in found}
    return [
        ItemResult(str(i), "refused", "NOT_FOUND", "Document not found.")
        for i in ids
        if i not in found_ids
    ]


def bulk_delete(
    db: Session,
    *,
    organization_id: uuid.UUID,
    workspace_id: uuid.UUID,
    ids: Sequence[uuid.UUID],
) -> list[ItemResult]:
    from app import crud

    items = _scoped_items(db, workspace_id=workspace_id, ids=ids)
    results: list[ItemResult] = _missing(ids, items)

    blocks = retention_service.blocking_reasons(
        db,
        organization_id=organization_id,
        workspace_id=workspace_id,
        work_items=items,
    )

    for item in items:
        block = blocks.get(item.id)
        if block is not None:
            results.append(
                ItemResult(str(item.id), "refused", block.code, block.reason)
            )
            continue
        try:
            crud.delete_work_item(db, db_obj=item)
            results.append(ItemResult(str(item.id), "ok"))
        except Exception as exc:  # noqa: BLE001 - one refusal must not stop the rest
            logger.warning(
                "bulk.delete_failed",
                extra={"work_item_id": str(item.id), "error": str(exc)},
            )
            results.append(
                ItemResult(str(item.id), "refused", "DELETE_FAILED", str(exc)[:500])
            )
    return results


def bulk_tag(
    db: Session,
    *,
    workspace_id: uuid.UUID,
    ids: Sequence[uuid.UUID],
    tags: Sequence[str],
    remove: bool = False,
    user_id: Optional[uuid.UUID] = None,
) -> list[ItemResult]:
    normalised = [normalise_tag(tag) for tag in tags]
    if not normalised:
        raise BulkError("TAG_REQUIRED", "Name at least one tag.")

    items = _scoped_items(db, workspace_id=workspace_id, ids=ids)
    results: list[ItemResult] = _missing(ids, items)

    for item in items:
        existing = set(
            db.execute(
                select(WorkItemTag.tag).where(WorkItemTag.work_item_id == item.id)
            ).scalars()
        )
        if remove:
            for tag in normalised:
                row = db.execute(
                    select(WorkItemTag).where(
                        WorkItemTag.work_item_id == item.id,
                        WorkItemTag.workspace_id == workspace_id,
                        WorkItemTag.tag == tag,
                    )
                ).scalar_one_or_none()
                if row is not None:
                    db.delete(row)
            results.append(ItemResult(str(item.id), "ok"))
            continue

        wanted = [tag for tag in normalised if tag not in existing]
        if len(existing) + len(wanted) > MAX_TAGS_PER_ITEM:
            results.append(
                ItemResult(
                    str(item.id),
                    "refused",
                    "TOO_MANY_TAGS",
                    f"A document may carry at most {MAX_TAGS_PER_ITEM} tags.",
                )
            )
            continue
        for tag in wanted:
            db.add(
                WorkItemTag(
                    work_item_id=item.id,
                    # From the loaded document, never from the request.
                    workspace_id=item.workspace_id,
                    tag=tag,
                    created_by_user_id=user_id,
                )
            )
        results.append(ItemResult(str(item.id), "ok"))
    db.flush()
    return results


def bulk_reprocess(
    db: Session,
    *,
    organization_id: uuid.UUID,
    workspace_id: uuid.UUID,
    ids: Sequence[uuid.UUID],
) -> list[ItemResult]:
    """Re-run extraction, through the same path the single-document button uses.

    Each document also re-fires `trigger.work_item.reprocessed`, so tenant rules
    behave identically whether one document or two hundred were reprocessed.
    """
    from app.services import job_service, outbox_service

    items = _scoped_items(db, workspace_id=workspace_id, ids=ids)
    results: list[ItemResult] = _missing(ids, items)

    for item in items:
        if item.uploaded_file_id is None:
            results.append(
                ItemResult(
                    str(item.id),
                    "refused",
                    "NO_SOURCE_FILE",
                    "This document has no stored file to re-extract.",
                )
            )
            continue
        try:
            job_service.enqueue(
                db,
                job_type="document.extract",
                payload={
                    "work_item_id": str(item.id),
                    "uploaded_file_id": str(item.uploaded_file_id),
                    "storage_key": item.stored_filename,
                    "mime_type": item.file_type,
                    "page_count": item.page_count,
                },
                organization_id=organization_id,
                idempotency_key=f"document.extract:{item.id}:{uuid.uuid4()}",
            )
            outbox_service.emit_trigger(
                db,
                organization_id=organization_id,
                workspace_id=workspace_id,
                event_type="trigger.work_item.reprocessed",
                resource_id=item.id,
                payload={
                    "work_item_id": str(item.id),
                    "original_filename": item.original_filename,
                },
            )
            results.append(ItemResult(str(item.id), "ok"))
        except Exception as exc:  # noqa: BLE001
            results.append(
                ItemResult(str(item.id), "refused", "ENQUEUE_FAILED", str(exc)[:500])
            )
    db.flush()
    return results


def build_export(
    db: Session,
    *,
    workspace_id: uuid.UUID,
    ids: Sequence[uuid.UUID],
    fmt: str = "csv",
) -> tuple[str, str]:
    """Return (mime_type, body) for the extracted fields of these documents.

    CSV columns are the union of every extracted key across the selection, so a
    spreadsheet has one column per field rather than one JSON blob per row.
    """
    items = _scoped_items(db, workspace_id=workspace_id, ids=ids)
    rows: list[dict[str, Any]] = []
    for item in items:
        entities = item.extracted_entities or {}
        flat: dict[str, Any] = {
            "work_item_id": str(item.id),
            "filename": item.original_filename,
            "status": item.status,
            "page_count": item.page_count,
        }
        if isinstance(entities, dict):
            for key, value in entities.items():
                flat[f"field.{key}"] = (
                    json.dumps(value, ensure_ascii=False)
                    if isinstance(value, (dict, list))
                    else value
                )
        rows.append(flat)

    if fmt == "json":
        return "application/json", json.dumps(rows, ensure_ascii=False, indent=2)

    columns: list[str] = []
    for row in rows:
        for key in row:
            if key not in columns:
                columns.append(key)
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=columns, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow(row)
    return "text/csv", buffer.getvalue()


def run(
    db: Session,
    *,
    organization_id: uuid.UUID,
    workspace_id: uuid.UUID,
    action: str,
    ids: Sequence[uuid.UUID],
    tags: Sequence[str] = (),
    export_format: str = "csv",
    user_id: Optional[uuid.UUID] = None,
) -> dict[str, Any]:
    if action not in BULK_ACTIONS:
        raise BulkError("UNKNOWN_ACTION", f"{action!r} is not a bulk action.")
    if not ids:
        raise BulkError("NO_IDS", "Select at least one document.")
    if len(ids) > MAX_BULK_IDS:
        raise BulkError(
            "TOO_MANY_IDS",
            f"A bulk request may name at most {MAX_BULK_IDS} documents.",
        )

    if action == "delete":
        results = bulk_delete(
            db,
            organization_id=organization_id,
            workspace_id=workspace_id,
            ids=ids,
        )
    elif action == "tag":
        results = bulk_tag(
            db, workspace_id=workspace_id, ids=ids, tags=tags, user_id=user_id
        )
    elif action == "reprocess":
        results = bulk_reprocess(
            db,
            organization_id=organization_id,
            workspace_id=workspace_id,
            ids=ids,
        )
    else:
        mime, body = build_export(
            db, workspace_id=workspace_id, ids=ids, fmt=export_format
        )
        return {
            "action": action,
            "succeeded": len(ids),
            "refused": 0,
            "results": [ItemResult(str(i), "ok").as_dict() for i in ids],
            "export": {"mime_type": mime, "body": body},
        }

    return {
        "action": action,
        "succeeded": sum(1 for r in results if r.outcome == "ok"),
        "refused": sum(1 for r in results if r.outcome == "refused"),
        "results": [r.as_dict() for r in results],
    }


def tags_for(
    db: Session, *, workspace_id: uuid.UUID, work_item_ids: Sequence[uuid.UUID]
) -> dict[str, list[str]]:
    if not work_item_ids:
        return {}
    rows = db.execute(
        select(WorkItemTag.work_item_id, WorkItemTag.tag).where(
            WorkItemTag.workspace_id == workspace_id,
            WorkItemTag.work_item_id.in_(list(work_item_ids)),
        )
    ).all()
    out: dict[str, list[str]] = {}
    for work_item_id, tag in rows:
        out.setdefault(str(work_item_id), []).append(tag)
    for value in out.values():
        value.sort()
    return out


def workspace_tags(db: Session, *, workspace_id: uuid.UUID) -> list[str]:
    return sorted(
        set(
            db.execute(
                select(WorkItemTag.tag).where(
                    WorkItemTag.workspace_id == workspace_id
                )
            ).scalars()
        )
    )


__all__ = [
    "BULK_ACTIONS",
    "BulkError",
    "ItemResult",
    "MAX_BULK_IDS",
    "MAX_TAGS_PER_ITEM",
    "build_export",
    "bulk_delete",
    "bulk_reprocess",
    "bulk_tag",
    "normalise_tag",
    "run",
    "tags_for",
    "workspace_tags",
]

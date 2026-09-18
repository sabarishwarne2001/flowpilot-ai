"""ARCH-38 — ingestion batches.

THE ONE RULE THIS MODULE EXISTS TO ENFORCE
==========================================

A failed file never stops the others. `process_items` wraps **each iteration**
in its own `try`, and each iteration commits or rolls back on its own. The
pre-ARCH-38 upload loop had the whole `for` inside one `try` (frontend F7), so
the first failure silently abandoned every file after it -- the user saw eleven
of fifty documents appear and no error explaining the other thirty-nine.

`verify_arch38.py` mutates this file by hoisting the `try` back out of the loop,
and gate B3 must die on that mutation. If the try/except is ever moved out of
the loop body for readability, that gate is what will stop it.

BATCH TERMINAL STATE
====================

`completed_items + failed_items = total_items` is what ends a batch, and the
database CHECK `ck_ingestion_batches_counts_bounded` refuses a count that
overshoots. Ending it emits `trigger.batch.completed`, which ARCH-37 reserved
in `INTERNAL_EVENT_TYPES` and in the outbox visibility CHECK but deliberately
kept out of the catalog until something emitted it. This module is that
something.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Callable, Iterable, Optional, Sequence

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.models.ingestion import (
    IngestionBatch,
    IngestionBatchItem,
    TERMINAL_BATCH_STATUSES,
)

logger = logging.getLogger("app.services.ingestion.batch")

BATCH_COMPLETED_EVENT = "trigger.batch.completed"

#: How many files one drop may hold. Beyond this the browser is asked to split,
#: rather than the server holding a transaction open over thousands of rows.
MAX_BATCH_ITEMS = 2_000


class BatchError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _now() -> datetime:
    return datetime.now(timezone.utc)


def create_batch(
    db: Session,
    *,
    workspace_id: uuid.UUID,
    user_id: Optional[uuid.UUID],
    source: str = "FILES",
    files: Sequence[dict[str, object]] = (),
) -> IngestionBatch:
    """Open a batch and its item rows in one transaction.

    `client_key` is the browser's own identifier for a file inside the drop. It
    is unique per batch (database constraint), which makes the whole creation
    idempotent from the client's side: a retried POST with the same keys fails
    loudly rather than creating a second set of items.
    """
    if len(files) > MAX_BATCH_ITEMS:
        raise BatchError(
            "BATCH_TOO_LARGE",
            f"A batch may hold at most {MAX_BATCH_ITEMS} files; "
            f"{len(files)} were sent.",
        )

    batch = IngestionBatch(
        id=uuid.uuid4(),
        workspace_id=workspace_id,
        created_by_user_id=user_id,
        status="OPEN",
        source=source,
        total_items=len(files),
        completed_items=0,
        failed_items=0,
    )
    db.add(batch)
    db.flush([batch])

    seen: set[str] = set()
    for entry in files:
        client_key = str(entry.get("client_key") or "").strip()
        if not client_key:
            raise BatchError("CLIENT_KEY_REQUIRED", "Every file needs a client key.")
        if client_key in seen:
            raise BatchError(
                "CLIENT_KEY_DUPLICATE",
                f"client_key {client_key!r} appears twice in one batch.",
            )
        seen.add(client_key)

        db.add(
            IngestionBatchItem(
                id=uuid.uuid4(),
                batch_id=batch.id,
                # Taken from the batch, never from the request: an item cannot
                # name a workspace of its own. The composite FK would refuse it
                # anyway; this makes the service refuse it first, with a
                # message rather than a 23503.
                workspace_id=batch.workspace_id,
                client_key=client_key[:200],
                filename=str(entry.get("filename") or "untitled")[:255],
                size_bytes=int(entry.get("size_bytes") or 0),
                sha256=(str(entry["sha256"]).lower() if entry.get("sha256") else None),
                status="PENDING",
            )
        )
    db.flush()
    return batch


def add_items(
    db: Session,
    *,
    batch: IngestionBatch,
    files: Sequence[dict[str, object]],
) -> list[IngestionBatchItem]:
    """Append items to an open batch, raising `total_items` to match.

    Used by archive expansion: the batch is created with one item (the archive)
    and grows to hold what came out of it.
    """
    if batch.status in TERMINAL_BATCH_STATUSES:
        raise BatchError("BATCH_CLOSED", "This batch has already finished.")
    if batch.total_items + len(files) > MAX_BATCH_ITEMS:
        raise BatchError(
            "BATCH_TOO_LARGE",
            f"A batch may hold at most {MAX_BATCH_ITEMS} files.",
        )

    created: list[IngestionBatchItem] = []
    for entry in files:
        item = IngestionBatchItem(
            id=uuid.uuid4(),
            batch_id=batch.id,
            workspace_id=batch.workspace_id,
            client_key=str(entry["client_key"])[:200],
            filename=str(entry.get("filename") or "untitled")[:255],
            size_bytes=int(entry.get("size_bytes") or 0),
            sha256=(str(entry["sha256"]).lower() if entry.get("sha256") else None),
            status="PENDING",
        )
        db.add(item)
        created.append(item)

    batch.total_items = batch.total_items + len(files)
    db.flush()
    return created


def assert_item_in_workspace(
    db: Session, *, workspace_id: uuid.UUID, item_id: uuid.UUID
) -> IngestionBatchItem:
    """The service half of the cross-workspace gate.

    The database refuses a mismatched INSERT through the composite foreign key.
    This refuses a *read* from the wrong tenant, with the ARCH-14 §14.7 shape:
    a row in another workspace is indistinguishable from a row that does not
    exist.
    """
    row = db.execute(
        select(IngestionBatchItem).where(
            IngestionBatchItem.id == item_id,
            IngestionBatchItem.workspace_id == workspace_id,
        )
    ).scalar_one_or_none()
    if row is None:
        raise BatchError("ITEM_NOT_FOUND", "Batch item not found.")
    return row


def mark_item_uploaded(
    db: Session,
    *,
    item: IngestionBatchItem,
    upload_session_id: uuid.UUID,
    sha256: Optional[str] = None,
) -> IngestionBatchItem:
    item.upload_session_id = upload_session_id
    item.status = "UPLOADED"
    if sha256:
        item.sha256 = sha256.lower()
    item.updated_at = _now()
    db.flush([item])
    return item


def record_success(
    db: Session, *, item: IngestionBatchItem, work_item_id: uuid.UUID
) -> IngestionBatch:
    item.status = "COMPLETED"
    item.work_item_id = work_item_id
    item.error_code = None
    item.error_detail = None
    item.updated_at = _now()
    db.flush([item])
    return _bump(db, batch_id=item.batch_id, completed=1, failed=0)


def record_container_expanded(
    db: Session, *, item: IngestionBatchItem
) -> IngestionBatch:
    """Close the archive's own item once its members have been enrolled.

    An archive is a container, not a document: it never becomes a work item, so
    it completes with `work_item_id` NULL. Giving it a synthetic work item id
    would put a row in the documents table that no file backs.
    """
    item.status = "COMPLETED"
    item.error_code = None
    item.error_detail = None
    item.updated_at = _now()
    db.flush([item])
    return _bump(db, batch_id=item.batch_id, completed=1, failed=0)


def record_failure(
    db: Session,
    *,
    item: IngestionBatchItem,
    error_code: str,
    error_detail: str = "",
) -> IngestionBatch:
    item.status = "FAILED"
    # The CHECK `ck_ingestion_batch_items_failed_has_code` refuses a FAILED row
    # with no code, so "it failed and nobody wrote down why" is not a state
    # this table can hold.
    item.error_code = (error_code or "UNKNOWN")[:64]
    item.error_detail = error_detail[:4000] or None
    item.updated_at = _now()
    db.flush([item])
    return _bump(db, batch_id=item.batch_id, completed=0, failed=1)


def _bump(
    db: Session, *, batch_id: uuid.UUID, completed: int, failed: int
) -> IngestionBatch:
    """Move the counters in SQL, not in Python.

    Two workers finishing two files at the same moment would both read
    `completed_items = 7` and both write 8. An UPDATE ... SET x = x + 1 is a
    single statement the database serialises.
    """
    db.execute(
        update(IngestionBatch)
        .where(IngestionBatch.id == batch_id)
        .values(
            completed_items=IngestionBatch.completed_items + completed,
            failed_items=IngestionBatch.failed_items + failed,
            status=func.coalesce(
                func.nullif(IngestionBatch.status, "OPEN"), "PROCESSING"
            ),
        )
    )
    db.flush()
    batch = db.execute(
        select(IngestionBatch).where(IngestionBatch.id == batch_id)
    ).scalar_one()
    db.refresh(batch)
    return batch


def finalize_if_done(
    db: Session, *, batch: IngestionBatch, organization_id: uuid.UUID
) -> Optional[str]:
    """Close a batch whose items have all reported, and emit its trigger.

    Returns the terminal status, or None if the batch is still running.
    """
    if batch.status in TERMINAL_BATCH_STATUSES:
        return None
    if batch.completed_items + batch.failed_items < batch.total_items:
        return None

    terminal = "COMPLETED_WITH_ERRORS" if batch.failed_items else "COMPLETED"
    batch.status = terminal
    batch.completed_at = _now()
    db.flush([batch])

    # ARCH38-S1:batch-completed-trigger. `emit_trigger` writes the INTERNAL
    # event and its `automation.execute` job in this transaction. Nothing
    # relays INTERNAL events (OUTBOX_INTERNAL_QUEUE has no claimer), so an
    # event written without its job is an event no rule ever sees.
    #
    # One event per batch, not one per file: the per-file trigger is
    # `trigger.work_item.created`, which `ingest_validated` already fires. A
    # 500-file drop therefore adds one automation execution, not 500, and the
    # ARCH-13 budget and cycle detector see it as one root.
    from app.services import outbox_service

    outbox_service.emit_trigger(
        db,
        organization_id=organization_id,
        workspace_id=batch.workspace_id,
        event_type=BATCH_COMPLETED_EVENT,
        resource_id=batch.id,
        payload={
            "batch_id": str(batch.id),
            "source": batch.source,
            "total_items": int(batch.total_items),
            "completed_items": int(batch.completed_items),
            "failed_items": int(batch.failed_items),
            "had_failures": bool(batch.failed_items),
        },
        idempotency_key=f"{BATCH_COMPLETED_EVENT}:{batch.id}",
    )
    logger.info(
        "ingestion.batch_finalized",
        extra={
            "batch_id": str(batch.id),
            "status": terminal,
            "completed": batch.completed_items,
            "failed": batch.failed_items,
        },
    )
    return terminal


def process_items(
    db: Session,
    *,
    batch: IngestionBatch,
    items: Iterable[IngestionBatchItem],
    organization_id: uuid.UUID,
    handler: Callable[[IngestionBatchItem], uuid.UUID],
) -> dict[str, int]:
    """Run `handler` over every item, isolating each failure.

    ARCH38-S1:per-item-isolation. The `try` is inside the loop. Hoisting it out
    -- which is what the pre-ARCH-38 frontend did and what verify_arch38's
    mutant M2 restores -- means the first failure abandons every item after it.
    Each iteration also commits on its own, so a crash mid-batch leaves the
    items that already finished recorded rather than rolled back.
    """
    succeeded = 0
    failed = 0

    for item in items:
        try:
            work_item_id = handler(item)
            record_success(db, item=item, work_item_id=work_item_id)
            db.commit()
            succeeded += 1
        except Exception as exc:  # noqa: BLE001 - one file must not stop the rest
            db.rollback()
            code = getattr(exc, "code", None) or type(exc).__name__
            try:
                fresh = db.execute(
                    select(IngestionBatchItem).where(
                        IngestionBatchItem.id == item.id,
                        IngestionBatchItem.workspace_id == batch.workspace_id,
                    )
                ).scalar_one()
                record_failure(
                    db, item=fresh, error_code=str(code), error_detail=str(exc)
                )
                db.commit()
            except Exception:  # noqa: BLE001 - recording a failure must not raise
                db.rollback()
                logger.exception(
                    "ingestion.failure_not_recorded",
                    extra={"item_id": str(item.id)},
                )
            failed += 1
            logger.warning(
                "ingestion.item_failed",
                extra={
                    "item_id": str(item.id),
                    "batch_id": str(batch.id),
                    "error_code": str(code),
                },
            )

    fresh_batch = db.execute(
        select(IngestionBatch).where(IngestionBatch.id == batch.id)
    ).scalar_one()
    db.refresh(fresh_batch)
    finalize_if_done(db, batch=fresh_batch, organization_id=organization_id)
    db.commit()

    return {"succeeded": succeeded, "failed": failed}


def get_batch(
    db: Session, *, workspace_id: uuid.UUID, batch_id: uuid.UUID
) -> IngestionBatch:
    row = db.execute(
        select(IngestionBatch).where(
            IngestionBatch.id == batch_id,
            IngestionBatch.workspace_id == workspace_id,
        )
    ).scalar_one_or_none()
    if row is None:
        raise BatchError("BATCH_NOT_FOUND", "Batch not found.")
    return row


def list_items(
    db: Session,
    *,
    workspace_id: uuid.UUID,
    batch_id: uuid.UUID,
    failed_only: bool = False,
) -> list[IngestionBatchItem]:
    statement = select(IngestionBatchItem).where(
        IngestionBatchItem.batch_id == batch_id,
        IngestionBatchItem.workspace_id == workspace_id,
    )
    if failed_only:
        statement = statement.where(IngestionBatchItem.status == "FAILED")
    return list(db.execute(statement.order_by(IngestionBatchItem.created_at)).scalars())


__all__ = [
    "BATCH_COMPLETED_EVENT",
    "BatchError",
    "MAX_BATCH_ITEMS",
    "add_items",
    "assert_item_in_workspace",
    "create_batch",
    "finalize_if_done",
    "get_batch",
    "list_items",
    "mark_item_uploaded",
    "process_items",
    "record_container_expanded",
    "record_failure",
    "record_success",
]

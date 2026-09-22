"""ARCH-38 — worker handlers for batch ingestion.

Three job types, all on the LIGHT profile:

* `batch.expand_archive` — read a zip from storage, enforce every limit, and
  ingest each member through `document_intake_service.ingest_validated` so the
  per-file `trigger.work_item.created` still fires for every one.
* `work_items.bulk` — run a bulk action off the request path. A delete of two
  hundred documents touches two hundred rows and their storage objects; doing
  that inside an HTTP request is how a gateway timeout leaves a half-finished
  delete.
* `ingestion.sweep_sessions` — abandon multipart uploads past their TTL, so a
  browser that closed mid-upload does not leave parts in the bucket forever.

REGISTRATION IS NOT BOOKKEEPING
===============================

Every job type here is also listed on the LIGHT profile in
`app/workers/profiles.py`. `assert_imports_match_profile()` raises ProfileError
at EVERY worker's startup on a handler no profile claims, so registering these
without the profile entry stops the entire fleet booting -- the defect ARCH-16
shipped and ARCH-25, 26, 27, 31, 32, 34 and 35 each recorded above their own
entries.
"""

from __future__ import annotations

import hashlib
import io
import logging
import uuid
from typing import Any

from sqlalchemy import select

from app.core.config import settings
from app.core.storage import ObjectNotFoundError, get_storage_driver
from app.db.session import SessionLocal
from app.models.ingestion import IngestionBatch, IngestionBatchItem
from app.services import document_intake_service, file_validation_service
from app.services.ingestion import (
    archive,
    batch_service,
    bulk_service,
    upload_session_service,
)

logger = logging.getLogger("app.workers.handlers.ingestion")


def handle_batch_expand_archive(payload: dict[str, Any]) -> dict[str, Any]:
    """Expand a zip that has already landed in object storage.

    The archive itself is one batch item. Its members become further items on
    the same batch, so the tray shows "1 archive -> 37 documents" as one drop
    rather than two unrelated things.
    """
    batch_id = uuid.UUID(str(payload["batch_id"]))
    item_id = uuid.UUID(str(payload["item_id"]))
    workspace_id = uuid.UUID(str(payload["workspace_id"]))
    organization_id = uuid.UUID(str(payload["organization_id"]))
    storage_key = str(payload["storage_key"])
    uploader_id = payload.get("uploader_id")
    uploader = uuid.UUID(str(uploader_id)) if uploader_id else None

    db = SessionLocal()
    try:
        batch = batch_service.get_batch(db, workspace_id=workspace_id, batch_id=batch_id)
        archive_item = batch_service.assert_item_in_workspace(
            db, workspace_id=workspace_id, item_id=item_id
        )

        driver = get_storage_driver()
        try:
            blob = driver.get(storage_key)
        except ObjectNotFoundError:
            batch_service.record_failure(
                db,
                item=archive_item,
                error_code="ARCHIVE_MISSING",
                error_detail="The uploaded archive is no longer in storage.",
            )
            db.commit()
            return {"expanded": 0, "failed": 1}

        try:
            entries = list(archive.expand(blob, depth=1))
        except archive.ArchiveRejected as exc:
            # The whole archive is refused. Its single item fails with the
            # limit's own code, so the failures drawer says "compression ratio"
            # rather than "something went wrong".
            batch_service.record_failure(
                db, item=archive_item, error_code=exc.code, error_detail=exc.message
            )
            batch_service.finalize_if_done(
                db, batch=batch, organization_id=organization_id
            )
            db.commit()
            logger.warning(
                "ingestion.archive_rejected",
                extra={"batch_id": str(batch_id), "code": exc.code},
            )
            return {"expanded": 0, "failed": 1, "code": exc.code}

        created = batch_service.add_items(
            db,
            batch=batch,
            files=[
                {
                    "client_key": f"{archive_item.client_key}:{index}",
                    "filename": entry.name,
                    "size_bytes": entry.size,
                    "sha256": hashlib.sha256(entry.data).hexdigest(),
                }
                for index, entry in enumerate(entries)
            ],
        )
        batch_service.record_container_expanded(db, item=archive_item)
        db.commit()

        by_key = {item.client_key: item for item in created}

        def _ingest(item: IngestionBatchItem) -> uuid.UUID:
            index = int(item.client_key.rsplit(":", 1)[1])
            entry = entries[index]
            spool = io.BytesIO(entry.data)
            validated = file_validation_service.validate_spooled(
                spool,
                len(entry.data),
                declared_mime=entry.mime_type,
                original_filename=entry.name,
                # ARCH40-S2:d13-batch-enforcement (hardening tier 3). Every
                # member of a batch archive obeys the workspace file-type
                # setting, exactly as a single upload does.
                allowed_mimes=file_validation_service.workspace_allowed_mimes(
                    db, workspace_id, settings.ALLOWED_MIME_TYPES
                ),
                max_pages=settings.MAX_DOCUMENT_PAGES,
                scrub_metadata=settings.SCRUB_UPLOAD_METADATA,
            )
            with validated:
                result = document_intake_service.ingest_validated(
                    db,
                    validated,
                    organization_id=organization_id,
                    workspace_id=workspace_id,
                    uploader_id=uploader,
                    enqueue_extraction=True,
                )
            return result.work_item.id

        outcome = batch_service.process_items(
            db,
            batch=batch,
            items=[by_key[key] for key in sorted(by_key)],
            organization_id=organization_id,
            handler=_ingest,
        )
        return {"expanded": outcome["succeeded"], "failed": outcome["failed"]}
    finally:
        db.close()


def handle_work_items_bulk(payload: dict[str, Any]) -> dict[str, Any]:
    """Run a bulk action off the request path."""
    workspace_id = uuid.UUID(str(payload["workspace_id"]))
    organization_id = uuid.UUID(str(payload["organization_id"]))
    action = str(payload["action"])
    ids = [uuid.UUID(str(value)) for value in payload.get("ids", [])]
    tags = [str(value) for value in payload.get("tags", [])]
    user_id = payload.get("user_id")

    db = SessionLocal()
    try:
        outcome = bulk_service.run(
            db,
            organization_id=organization_id,
            workspace_id=workspace_id,
            action=action,
            ids=ids,
            tags=tags,
            export_format=str(payload.get("export_format") or "csv"),
            user_id=uuid.UUID(str(user_id)) if user_id else None,
        )
        db.commit()
        # The export body is not returned through the job record: a job result
        # is stored, and storing a CSV of a tenant's extracted fields in the
        # jobs table would put document content somewhere with none of the
        # retention rules documents have.
        outcome.pop("export", None)
        return outcome
    finally:
        db.close()


def handle_sweep_upload_sessions(payload: dict[str, Any]) -> dict[str, Any]:
    db = SessionLocal()
    try:
        swept = upload_session_service.sweep_expired(db)
        db.commit()
        return {"swept": swept}
    finally:
        db.close()


__all__ = [
    "handle_batch_expand_archive",
    "handle_sweep_upload_sessions",
    "handle_work_items_bulk",
]

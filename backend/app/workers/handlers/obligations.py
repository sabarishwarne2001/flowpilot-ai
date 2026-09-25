"""ARCH46-S1:handler — the `obligations.extract_document` job.

Enqueued by app/services/post_enrichment.dispatch_in_session after a document
is enriched (and by "Read again" in the console), a little later than the
ARCH-42 resolution of the same document so its parties are usually linked.
Reading obligations is clause segmentation, typed values, ARCH-33's notice
reader and date arithmetic -- no model, OCR or PDF engine -- so the job runs
on the LIGHT worker profile (app/workers/profiles.py).

Idempotent: extraction upserts by source key, so a retry or a second dispatch
creates nothing new. An organization without capability.obligations is
skipped (nothing is read, nothing is billed).
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

logger = logging.getLogger("app.workers.handlers.obligations")

JOB_TYPE = "obligations.extract_document"


def handle_extract_document(payload: dict[str, Any]) -> dict[str, Any]:
    from app.db.session import SessionLocal
    from app.models.work_item import WorkItem
    from app.services.obligations import gate, service

    raw = payload.get("work_item_id")
    if not raw:
        raise ValueError("obligations.extract_document requires work_item_id")
    work_item_id = uuid.UUID(str(raw))
    with SessionLocal() as db:
        item = db.get(WorkItem, work_item_id)
        if item is None:
            return {"extracted": False, "reason": "the document no longer exists"}
        organization_id = gate.organization_of(db, item)
        if not gate.capability_held(db, organization_id):
            return {"extracted": False, "reason": "capability.obligations not held"}
        summary = service.extract_for_work_item(db, work_item=item, organization_id=organization_id)
        db.commit()
    logger.info("obligations.extracted", extra={"work_item_id": str(work_item_id), **summary.as_json()})
    return {"extracted": True, "work_item_id": str(work_item_id), **summary.as_json()}


__all__ = ["JOB_TYPE", "handle_extract_document"]

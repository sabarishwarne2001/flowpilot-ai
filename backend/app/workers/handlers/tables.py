"""ARCH44-S1:handler — the `tables.extract_document` job.

Enqueued by app/services/post_enrichment.py after enrichment, for documents
of an extractable type in organizations whose plan carries
capability.table_intelligence (and by the API for documents too long to
extract inline). It reads the PDF text layer and renders scanned pages to
find ruling lines -- PDF-engine work, which is why it runs on the OCR worker
profile (app/workers/profiles.py), never on LIGHT.

Idempotent: a retry replaces the document's tables with the same result;
tables a person has corrected or decided are kept (service.TableError
KEPT_CORRECTIONS) unless the payload says force.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

logger = logging.getLogger("app.workers.handlers.tables")

JOB_TYPE = "tables.extract_document"


def handle_extract_document(payload: dict[str, Any]) -> dict[str, Any]:
    from app.db.session import SessionLocal
    from app.models.work_item import WorkItem
    from app.services.tables import gate, service

    raw = payload.get("work_item_id")
    if not raw:
        raise ValueError("tables.extract_document requires work_item_id")
    with SessionLocal() as db:
        item = db.get(WorkItem, uuid.UUID(str(raw)))
        if item is None:
            return {"extracted": False, "reason": "work item no longer exists"}
        if not gate.capability_held(db, gate.organization_of(db, item)):
            return {"extracted": False, "reason": "capability.table_intelligence not held"}
        try:
            result = service.extract_document(db, work_item=item, force=bool(payload.get("force")))
        except service.TableError as exc:
            db.rollback()
            return {"extracted": False, "reason": exc.code}
        db.commit()
    logger.info("tables.extracted", extra={"work_item_id": str(raw), "result": result})
    return result


__all__ = ["JOB_TYPE", "handle_extract_document"]

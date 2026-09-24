"""ARCH42-S1:handler — the `entities.resolve_document` job.

Enqueued by app/services/post_enrichment.py right after enrichment emits
`work_item.enriched`, for organizations whose plan carries
capability.entity_graph. Resolution is integer arithmetic, string similarity
and indexed lookups — no model, no OCR, no PDF engine — so it runs on the
LIGHT worker profile (app/workers/profiles.py), which is why the name vectors
are hashed n-grams and not SentenceTransformer embeddings.

Idempotent: a retry, or a second enqueue for the same document, resolves the
same fields to the same records (resolver.spec_digest).
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

logger = logging.getLogger("app.workers.handlers.entities")

JOB_TYPE = "entities.resolve_document"


def handle_entities_resolve_document(payload: dict[str, Any]) -> dict[str, Any]:
    from app.db.session import SessionLocal
    from app.services.entities import resolver

    raw = payload.get("work_item_id")
    if not raw:
        raise ValueError("entities.resolve_document requires work_item_id")
    with SessionLocal() as db:
        result = resolver.resolve_by_id(db, work_item_id=uuid.UUID(str(raw)))
        db.commit()
    logger.info("entities.resolved", extra={"work_item_id": str(raw), "result": result})
    return result


__all__ = ["JOB_TYPE", "handle_entities_resolve_document"]

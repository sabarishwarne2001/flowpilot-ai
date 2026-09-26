"""ARCH47-S1:handler — the `erp.deliver_posting` job.

Enqueued when a posting is planned (a person's "Post", a target's auto-post
sweep, the erp.post Flow Builder action), when a retry falls due and when the
sweep reclaims an expired lease. Delivery is network and file work (HTTPS
through the SSRF-safe client, SFTP through paramiko) -- no model, OCR or PDF
engine -- so the job runs on the LIGHT worker profile (app/workers/profiles.py).

Safe to run twice or concurrently: the ledger claims the posting under a row
lock and a lease (app/services/erp/service.deliver). An organization without
capability.erp_posting is skipped: nothing is sent.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

logger = logging.getLogger("app.workers.handlers.erp")

JOB_TYPE = "erp.deliver_posting"


def handle_deliver_posting(payload: dict[str, Any]) -> dict[str, Any]:
    from app.db.session import SessionLocal
    from app.models.erp import ErpPosting
    from app.services.erp import gate, service

    raw = payload.get("posting_id")
    if not raw:
        raise ValueError("erp.deliver_posting requires posting_id")
    posting_id = uuid.UUID(str(raw))
    with SessionLocal() as db:
        posting = db.get(ErpPosting, posting_id)
        if posting is None:
            return {"delivered": False, "reason": "the posting no longer exists"}
        if not gate.capability_held(db, posting.organization_id):
            return {"delivered": False, "reason": "capability.erp_posting not held"}
        db.commit()  # the claim below locks the row in its own transaction
        result = service.deliver(db, posting_id)
    logger.info("erp.delivery", extra={"posting_id": str(posting_id), **{k: str(val) for k, val in result.items()}})
    return result


__all__ = ["JOB_TYPE", "handle_deliver_posting"]

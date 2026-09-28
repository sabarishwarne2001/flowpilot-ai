"""ARCH49-S1:handler — the `process.sweep_workspace` job.

Enqueued by scripts/sweep_process.py (every 15 minutes from
deploy/cron.d/flowpilot-sweepers, one job per workspace per slot: the
idempotency key is the workspace and the slot). One workspace's sweep: the
event log catches up, the SLA models are refitted (at most hourly) and their
predictions written, and the exception agent retires, applies and plans
(app/services/process_intel/pipeline.py). Queries, arithmetic and scikit-learn
on small tables -- no model, OCR or PDF engine -- so it runs on the LIGHT
worker profile (app/workers/profiles.py).

An organization without capability.process_intelligence is skipped.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

logger = logging.getLogger("app.workers.handlers.process")

JOB_TYPE = "process.sweep_workspace"


def handle_sweep_workspace(payload: dict[str, Any]) -> dict[str, Any]:
    from app.db.session import SessionLocal
    from app.services.process_intel import pipeline

    raw = payload.get("workspace_id")
    if not raw:
        raise ValueError("process.sweep_workspace requires workspace_id")
    workspace_id = uuid.UUID(str(raw))
    with SessionLocal() as db:
        try:
            report = pipeline.sweep_workspace(db, workspace_id=workspace_id)
        except LookupError:
            db.rollback()
            return {"swept": False, "reason": "the workspace no longer exists"}
        db.commit()
    logger.info("process.swept", extra={"workspace_id": str(workspace_id),
                                        "written": (report.get("ingest") or {}).get("written")})
    return {"swept": "skipped" not in report, "workspace_id": str(workspace_id),
            "written": (report.get("ingest") or {}).get("written", 0),
            "agent": report.get("agent"), "skipped": report.get("skipped")}


__all__ = ["JOB_TYPE", "handle_sweep_workspace"]

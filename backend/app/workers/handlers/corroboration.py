"""ARCH45-S1:handler — the `corroboration.run` job.

Enqueued by app/services/corroboration/service.request() when a comparison is
asked for and no current answer is cached. It aligns clauses with the
SentenceTransformer the platform already runs, so it is registered on the
ENRICH worker profile (app/workers/profiles.py), never on LIGHT (which forbids
sentence_transformers) or OCR.

Idempotent: a run that is no longer QUEUED (already computed, failed, deleted)
is skipped. A failure marks the run FAILED with the reason, visible in the
console, where "Re-run" asks again; it is not retried blindly, because the
engine is deterministic and would fail the same way.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

logger = logging.getLogger("app.workers.handlers.corroboration")

JOB_TYPE = "corroboration.run"


def handle_run(payload: dict[str, Any]) -> dict[str, Any]:
    from app.db.session import SessionLocal
    from app.models.corroboration import CorroborationRun
    from app.services.corroboration import gate, service
    from app.services.corroboration import vocabulary as v

    raw = payload.get("run_id")
    if not raw:
        raise ValueError("corroboration.run requires run_id")
    run_id = uuid.UUID(str(raw))
    with SessionLocal() as db:
        run = db.get(CorroborationRun, run_id)
        if run is None:
            return {"ran": False, "reason": "the comparison no longer exists"}
        if run.status != v.STATUS_QUEUED:
            return {"ran": False, "reason": f"the comparison is {run.status}"}
        if not gate.capability_held(db, run.organization_id):
            service.mark_failed(db, run_id=run.id, reason="capability.universal_corroborator is not on this plan")
            db.commit()
            return {"ran": False, "reason": "capability.universal_corroborator not held"}
        service.mark_running(db, run)
        db.commit()
        try:
            result = service.execute(db, run=run)
            db.commit()
        except Exception as exc:  # noqa: BLE001 - recorded on the run; the console shows it
            db.rollback()
            logger.exception("corroboration.failed", extra={"run_id": str(run_id)})
            service.mark_failed(db, run_id=run_id, reason=f"{type(exc).__name__}: {exc}")
            db.commit()
            return {"ran": False, "reason": "failed", "error": str(exc)[:500]}
    logger.info("corroboration.completed", extra={"run_id": str(run_id), "discrepancies": len(result.discrepancies)})
    return {"ran": True, "run_id": str(run_id), "discrepancies": len(result.discrepancies),
            "material": result.material_count}


__all__ = ["JOB_TYPE", "handle_run"]

"""ARCH49-S1:pipeline — one workspace's process-intelligence sweep, in order.

    1. ingest   the event log catches up with every source (and prunes past retention)
    2. predict  each SLA object type is refitted and predicted, at most once an hour
    3. agent    stale proposals retired, due auto-applies re-checked and applied,
                open exceptions planned

The job handler (`process.sweep_workspace`, LIGHT profile) calls this and
commits; `scripts/sweep_process.py` enqueues one job per enabled workspace per
15-minute slot (idempotent on the slot), and can run it inline.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Any, Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.services.process_intel import ingest, service, sla
from app.services.process_intel import vocabulary as v

#: An SLA model is refitted at most this often per workspace and object type.
REFIT_MINUTES = 60


def refit_due(db: Session, *, workspace_id: uuid.UUID, object_type: str, now: datetime) -> bool:
    last = db.execute(text("SELECT max(trained_at) FROM process_model_runs WHERE workspace_id = :w AND object_type = :t"),
                      {"w": workspace_id, "t": object_type}).scalar()
    return last is None or now - last >= timedelta(minutes=REFIT_MINUTES)


def sweep_workspace(db: Session, *, workspace_id: uuid.UUID, organization_id: Optional[uuid.UUID] = None,
                    at: Optional[datetime] = None, force_refit: bool = False) -> dict[str, Any]:
    from app.services.process_intel.agent import runner

    now = at or service.now()
    organization_id = organization_id or service.organization_of(db, workspace_id)
    if organization_id is None:
        raise LookupError("workspace not found")
    if not service.enabled_for(db, organization_id):
        return {"workspace_id": str(workspace_id), "skipped": "capability.process_intelligence not on the plan"}
    report: dict[str, Any] = {"workspace_id": str(workspace_id)}
    report["ingest"] = ingest.ingest(db, workspace_id=workspace_id, organization_id=organization_id, at=now)
    report["pruned"] = ingest.prune(db, workspace_id=workspace_id, at=now)
    report["sla"] = {}
    for object_type in v.SLA_OBJECT_TYPES:
        if force_refit or refit_due(db, workspace_id=workspace_id, object_type=object_type, now=now):
            report["sla"][object_type] = sla.run(db, workspace_id=workspace_id, organization_id=organization_id,
                                                 object_type=object_type, at=now)
    report["agent"] = runner.run(db, workspace_id=workspace_id, organization_id=organization_id, at=now)
    return report


__all__ = ["REFIT_MINUTES", "refit_due", "sweep_workspace"]

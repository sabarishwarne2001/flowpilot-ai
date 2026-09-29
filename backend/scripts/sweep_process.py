#!/usr/bin/env python3
# ARCH50-S1:executable. Carried by apply_arch50.py with its executable bit (git stored it 100644).
"""ARCH49-S1:sweep-process — the process-intelligence clock.

    python scripts/sweep_process.py              # report which workspaces would be swept (nothing written)
    python scripts/sweep_process.py --apply      # enqueue one process.sweep_workspace job per workspace
    python scripts/sweep_process.py --inline     # run every workspace's sweep here, committing per workspace

For every ACTIVE workspace of an organization holding
capability.process_intelligence, one `process.sweep_workspace` job per
15-minute slot (the idempotency key is the workspace and the slot, so running
this twice in a slot enqueues nothing new). The job (LIGHT worker profile):

  1. the object-centric event log catches up with every source (idempotent:
     each event is unique by its source and key) and prunes past retention;
  2. each SLA object type is refitted at most hourly (gradient boosting,
     Brier-checked on held-out instances) and its open instances predicted;
     an instance entering AT_RISK raises trigger.process.sla_at_risk once per
     due time;
  3. the exception agent retires stale proposals, re-checks and applies the
     scheduled auto-applies whose hold has passed, and plans open exceptions.

Scheduled every 15 minutes (deploy/cron.d/flowpilot-sweepers), dispatched by
`flowpilot-sweep process` (deploy/bin/flowpilot-sweep, RH-4 G14).
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))


def slot_of(moment: datetime) -> str:
    from app.services.process_intel import vocabulary as v

    minute = (moment.minute // v.SWEEP_SLOT_MINUTES) * v.SWEEP_SLOT_MINUTES
    return moment.strftime("%Y%m%dT%H") + f"{minute:02d}"


def sweep(db, *, apply: bool, inline: bool = False, at: Optional[datetime] = None) -> dict:
    from app.services import job_service
    from app.services.process_intel import pipeline, service
    from app.services.process_intel import vocabulary as v

    moment = at or service.now()
    if apply and not inline:
        # ARCH50-S1:sweep-registers-handlers. Run from cron this script is not the worker: without the handler
        # registry job_service.enqueue() refuses process.sweep_workspace (UnknownJobTypeError) as soon as one
        # workspace is enabled. Found by the GA-2 run; the ARCH-49 gate called register_all() itself.
        from app.workers.handlers import register_all

        register_all()
    if not db.in_transaction():
        db.begin()  # job_service.enqueue writes only inside a transaction (the caller's commit is the enqueue)
    targets = service.workspaces_enabled(db)
    report = {"workspaces": len(targets), "enqueued": 0, "swept": 0, "slot": slot_of(moment), "applied": apply}
    for workspace_id, organization_id in targets:
        if inline:
            pipeline.sweep_workspace(db, workspace_id=workspace_id, organization_id=organization_id, at=moment)
            db.commit()
            report["swept"] += 1
        elif apply:
            job_service.enqueue(db, job_type=v.JOB_SWEEP, organization_id=organization_id,
                                payload={"workspace_id": str(workspace_id)},
                                idempotency_key=f"{v.JOB_SWEEP}:{workspace_id}:{report['slot']}")
            report["enqueued"] += 1
    if apply and not inline:
        db.commit()
    elif not inline:
        db.rollback()
    return report


def main() -> int:
    from app.db.session import SessionLocal

    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--inline", action="store_true")
    args = parser.parse_args()
    with SessionLocal() as db:
        report = sweep(db, apply=args.apply, inline=args.inline, at=datetime.now(timezone.utc))
    print(json.dumps(report, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())

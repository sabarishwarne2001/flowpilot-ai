#!/usr/bin/env python3
"""ARCH50-S1:sweep-revops — the daily RevOps clock.

    python scripts/sweep_revops.py              # report what would run (nothing written)
    python scripts/sweep_revops.py --apply      # enqueue ONE revops.sweep job for today (idempotent on the date)
    python scripts/sweep_revops.py --inline     # run the sweep here and commit

Scheduled daily (deploy/cron.d/flowpilot-sweepers), dispatched by `flowpilot-sweep revops`. The job runs on the
LIGHT worker profile (app/workers/handlers/revops.py).
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

JOB_TYPE = "revops.sweep"


def sweep(db, *, apply: bool, inline: bool = False, at: Optional[datetime] = None) -> dict:
    from app.services import job_service
    from app.services.revops import metrics
    from app.services.revops.service import now
    from app.services.sovereign import egress_policy

    moment = at or now()
    day = moment.strftime("%Y%m%d")
    report: dict = {"day": day, "applied": apply, "enqueued": 0}
    if inline:
        report["sweep"] = metrics.sweep(db)
        report["sweep"]["refusals_pruned"] = egress_policy.prune_refusals(db)
        db.commit()
        return report
    if not db.in_transaction():
        db.begin()  # job_service.enqueue writes only inside a transaction
    if apply:
        # A cron-run script is not the worker: without the registry enqueue() refuses every job type.
        from app.workers.handlers import register_all

        register_all()
        # A platform job has no organization, and uq_jobs_org_idempotency_key does not cover NULL: two hosts running
        # the cron at once would both pass enqueue()'s look-up. The advisory lock makes the look-up and the insert
        # one step across every host.
        from sqlalchemy import text

        key = f"{JOB_TYPE}:{day}"
        db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:k))"), {"k": key})
        known = db.execute(text("SELECT 1 FROM jobs WHERE job_type = :t AND idempotency_key = :k AND organization_id "
                                "IS NULL"), {"t": JOB_TYPE, "k": key}).first()
        job = job_service.enqueue(db, job_type=JOB_TYPE, organization_id=None, payload={"day": day}, idempotency_key=key)
        report["job_id"] = str(job.id)
        report["enqueued"] = 0 if known else 1
        db.commit()
    else:
        db.rollback()
    return report


def main() -> int:
    from app.db.session import SessionLocal

    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--inline", action="store_true")
    args = parser.parse_args()
    with SessionLocal() as db:
        report = sweep(db, apply=args.apply, inline=args.inline)
    print(json.dumps(report, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())

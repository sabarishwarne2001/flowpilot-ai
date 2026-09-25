#!/usr/bin/env python3
"""ARCH45-S1:sweep-corroboration — nightly corroborator housekeeping.

    python scripts/sweep_corroboration.py            # report what would change
    python scripts/sweep_corroboration.py --apply    # apply it

  1. re-fingerprint every COMPLETED comparison: one whose documents changed
     since (a reviewer corrected a table cell, an entity merge moved a party,
     a document was reprocessed) becomes STALE -- the hooks catch most of this
     as it happens; this catches the rest;
  2. mark QUEUED / RUNNING comparisons older than STUCK_HOURS as FAILED (a
     worker died mid-run); "Re-run" in the console asks again;
  3. delete STALE and FAILED comparisons older than RETENTION_DAYS in which
     nobody decided a discrepancy (reviewer work is never deleted).

Dispatched by `flowpilot-sweep corroboration` (deploy/bin/flowpilot-sweep) and
scheduled in deploy/cron.d/flowpilot-sweepers (RH-4 G14).
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))


def sweep(db, *, apply: bool, limit: int = 5000) -> dict:
    from sqlalchemy import delete, exists, select

    from app.models.corroboration import CorroborationRun, Discrepancy
    from app.services.corroboration import service
    from app.services.corroboration import vocabulary as v

    now = datetime.now(timezone.utc)
    completed = list(db.execute(select(CorroborationRun).where(CorroborationRun.status == v.STATUS_COMPLETED)
                                .order_by(CorroborationRun.completed_at.asc()).limit(limit)).scalars())
    stale = service.invalidate(db, completed)
    stuck_before = now - timedelta(hours=v.STUCK_HOURS)
    stuck = list(db.execute(select(CorroborationRun).where(
        CorroborationRun.status.in_((v.STATUS_QUEUED, v.STATUS_RUNNING)),
        CorroborationRun.updated_at < stuck_before)).scalars())
    for run in stuck:
        service.mark_failed(db, run_id=run.id, reason=f"no result after {v.STUCK_HOURS} hours; re-run it")
    old = now - timedelta(days=v.RETENTION_DAYS)
    decided = exists().where(Discrepancy.run_id == CorroborationRun.id, Discrepancy.status != v.DECISION_OPEN)
    expired = list(db.execute(select(CorroborationRun.id).where(
        CorroborationRun.status.in_((v.STATUS_STALE, v.STATUS_FAILED)), CorroborationRun.updated_at < old,
        ~decided)).scalars())
    if expired:
        db.execute(delete(CorroborationRun).where(CorroborationRun.id.in_(expired))
                   .execution_options(synchronize_session=False))
    report = {"checked": len(completed), "stale": stale, "failed_stuck": len(stuck), "deleted": len(expired),
              "applied": apply}
    if apply:
        db.commit()
    else:
        db.rollback()
    return report


def main() -> int:
    from app.db.session import SessionLocal

    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--limit", type=int, default=5000)
    args = parser.parse_args()
    with SessionLocal() as db:
        report = sweep(db, apply=args.apply, limit=args.limit)
    print(json.dumps(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""ARCH46-S1:sweep-obligations — the obligations clock.

    python scripts/sweep_obligations.py            # report what would change (rolled back)
    python scripts/sweep_obligations.py --apply    # apply it

For every workspace with open obligations, in UTC, each at its OWN local date
(workspaces.timezone):

  1. OPEN -> DUE_SOON when today is within the obligation's lead days of its
     due date; -> OVERDUE from the local midnight after it; back to OPEN when a
     due date moved later;
  2. entering DUE_SOON or OVERDUE raises trigger.obligation.due_soon /
     trigger.obligation.overdue for Flow Builder EXACTLY ONCE per obligation,
     state and due date: a partial UNIQUE index on obligation_events refuses a
     second alert row, and the outbox idempotency key refuses a second event.
     Run it twice, or every hour: the second run changes and emits nothing;
  3. extracted obligations whose ARCH-42 party resolved after extraction are
     linked to it.

Scheduled HOURLY (deploy/cron.d/flowpilot-sweepers) so every zone's midnight is
caught within the hour; the per-workspace local date makes each run a nightly
sweep for that workspace. Dispatched by `flowpilot-sweep obligations`
(deploy/bin/flowpilot-sweep, RH-4 G14). Organizations without
capability.obligations are skipped.
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


def sweep(db, *, apply: bool, at: Optional[datetime] = None, limit: int = 50000) -> dict:
    from app.services.obligations import service

    report = service.sweep(db, at=at, limit=limit)
    report["applied"] = apply
    if apply:
        db.commit()
    else:
        db.rollback()
    return report


def main() -> int:
    from app.db.session import SessionLocal

    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--limit", type=int, default=50000)
    args = parser.parse_args()
    with SessionLocal() as db:
        report = sweep(db, apply=args.apply, limit=args.limit)
    print(json.dumps(report, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())

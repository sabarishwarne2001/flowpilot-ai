#!/usr/bin/env python3
"""ARCH47-S1:sweep-erp-postings — the ERP posting clock.

    python scripts/sweep_erp_postings.py            # report what would happen (rolled back)
    python scripts/sweep_erp_postings.py --apply    # do it

For every ACTIVE ERP target of an organization holding capability.erp_posting:

  1. AUTO-POST: approved outcomes (APPROVED three-way matches, ACCEPTED tables,
     COMPLETE cases) approved since the target's auto-post was switched on are
     planned for the object kinds the target auto-posts. Planning is the
     ledger's idempotent insert: a posting that exists is never planned twice,
     however often the sweep runs;
  2. RETRIES: postings whose backoff has elapsed, PENDING ones older than five
     minutes whose job was lost, and SENDING ones whose lease expired (the
     worker disappeared mid-send; the next claim PROBES before re-sending) are
     enqueued for delivery;
  3. ACKNOWLEDGEMENTS: SFTP targets are read for 997s and ack files naming the
     postings awaiting acknowledgement; a posting with no acknowledgement within
     the target's ack_timeout_hours is FAILED (the review hub, posting.failed).

Scheduled every 10 minutes (deploy/cron.d/flowpilot-sweepers), dispatched by
`flowpilot-sweep erp_postings` (deploy/bin/flowpilot-sweep, RH-4 G14).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))


def sweep(db, *, apply: bool) -> dict:
    from app.services.erp import service

    report = service.sweep(db, apply=apply).as_json()
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
    args = parser.parse_args()
    with SessionLocal() as db:
        report = sweep(db, apply=args.apply)
    print(json.dumps(report, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())

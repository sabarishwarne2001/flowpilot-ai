#!/usr/bin/env python3
"""ARCH43-S1:sweep-cases — nightly case-intelligence housekeeping.

    python scripts/sweep_cases.py            # report what would change
    python scripts/sweep_cases.py --apply    # expire requests, re-evaluate open cases

Dispatched by `flowpilot-sweep cases` (deploy/bin/flowpilot-sweep) and
scheduled in deploy/cron.d/flowpilot-sweepers (RH-4 G14). Re-evaluation picks
up documents whose fields a reviewer corrected since the case was graded.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))


def main() -> int:
    from sqlalchemy import select

    from app.db.session import SessionLocal
    from app.models.cases import Case
    from app.services.cases import assembly, requests
    from app.services.cases import vocabulary as v

    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--limit", type=int, default=5000)
    args = parser.parse_args()
    with SessionLocal() as db:
        expired = requests.expire(db)
        changed = 0
        cases = list(db.execute(select(Case).where(Case.status != v.CASE_CLOSED).order_by(Case.evaluated_at.asc().nullsfirst())
                                .limit(args.limit)).scalars())
        for case in cases:
            before = case.status
            if assembly.evaluate(db, case=case)["status"] != before:
                changed += 1
        report = {"expired_requests": expired, "cases_evaluated": len(cases), "status_changed": changed, "applied": args.apply}
        if args.apply:
            db.commit()
        else:
            db.rollback()
    print(json.dumps(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())

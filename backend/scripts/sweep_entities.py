#!/usr/bin/env python
"""ARCH42-S1:sweep-script — the nightly entity re-resolution pass.

    python scripts/sweep_entities.py            # dry run: report, roll back
    python scripts/sweep_entities.py --apply    # commit
    python scripts/sweep_entities.py --apply --workspace <uuid>

Dispatched by deploy/bin/flowpilot-sweep (`entities`) from
deploy/cron.d/flowpilot-sweepers. What it does is in
app/services/entities/sweep.py.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
import uuid

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Entity graph nightly sweep (ARCH-42)")
    parser.add_argument("--apply", action="store_true", help="commit (default: dry run)")
    parser.add_argument("--workspace", action="append", default=[], help="limit to these workspace ids")
    args = parser.parse_args(argv)

    from app.db.session import SessionLocal
    from app.services.entities import sweep

    with SessionLocal() as db:
        report = sweep.run(db, workspace_ids=[uuid.UUID(w) for w in args.workspace] or None)
        if args.apply:
            db.commit()
        else:
            db.rollback()
    report["applied"] = bool(args.apply)
    print(json.dumps(report, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())

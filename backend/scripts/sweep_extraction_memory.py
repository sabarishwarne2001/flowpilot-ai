#!/usr/bin/env python
"""ARCH41-S2:sweep-script — the nightly extraction-memory pass.

    python scripts/sweep_extraction_memory.py            # dry run: report, roll back
    python scripts/sweep_extraction_memory.py --apply    # commit
    python scripts/sweep_extraction_memory.py --apply --workspace <uuid>

Dispatched by deploy/bin/flowpilot-sweep (`extraction_memory`) from
deploy/cron.d/flowpilot-sweepers. What it does is in
app/services/extraction_memory/sweep.py.
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
    parser = argparse.ArgumentParser(description="Extraction memory nightly sweep (ARCH-41)")
    parser.add_argument("--apply", action="store_true", help="commit (default: dry run)")
    parser.add_argument("--workspace", action="append", default=[], help="limit to these workspace ids")
    args = parser.parse_args(argv)

    from app.db.session import SessionLocal
    from app.services.extraction_memory import sweep

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

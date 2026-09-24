#!/usr/bin/env python3
"""ARCH43-S1:queue-weights — the platform operator's control over fair claiming.

    python scripts/queue_weights.py list
    python scripts/queue_weights.py set <organization_id> --weight 2 [--max-inflight 40] [--note "..."]
    python scripts/queue_weights.py clear <organization_id>
    python scripts/queue_weights.py backlog          # waiting and in-flight jobs per organization

A weight is a share, not a priority: an organization with weight 2 receives
twice the claims of a weight-1 organization while both have work waiting, and
all of the capacity when it is alone. No row means weight 1. There is
deliberately no tenant-facing API: tenants must not set their own share.
"""

from __future__ import annotations

import argparse
import getpass
import sys
import uuid
from decimal import Decimal
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))


def main() -> int:
    from sqlalchemy import text

    from app.db.session import SessionLocal
    from app.models.packets import TenantQueueWeight

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list")
    sub.add_parser("backlog")
    s = sub.add_parser("set")
    s.add_argument("organization_id")
    s.add_argument("--weight", type=Decimal, required=True)
    s.add_argument("--max-inflight", type=int, default=None)
    s.add_argument("--note", default=None)
    c = sub.add_parser("clear")
    c.add_argument("organization_id")
    args = parser.parse_args()

    with SessionLocal() as db:
        if args.command == "list":
            for row in db.execute(text("SELECT w.organization_id, o.name, w.weight, w.max_inflight, w.updated_at, w.note "
                                       "FROM tenant_queue_weights w JOIN organizations o ON o.id = w.organization_id "
                                       "ORDER BY w.weight DESC")).all():
                print(f"{row.organization_id}  {row.name:<30}  weight={row.weight}  max_inflight={row.max_inflight}  {row.note or ''}")
            return 0
        if args.command == "backlog":
            for row in db.execute(text("SELECT organization_id, count(*) FILTER (WHERE status IN ('PENDING','FAILED')) AS waiting, "
                                       "count(*) FILTER (WHERE status = 'CLAIMED') AS running FROM jobs "
                                       "WHERE status IN ('PENDING','FAILED','CLAIMED') GROUP BY organization_id "
                                       "ORDER BY waiting DESC")).all():
                print(f"{row.organization_id or 'platform':<38} waiting={row.waiting:<7} running={row.running}")
            return 0
        org = uuid.UUID(args.organization_id)
        row = db.get(TenantQueueWeight, org)
        if args.command == "clear":
            if row is not None:
                db.delete(row)
            db.commit()
            print(f"{org}: weight cleared (default 1)")
            return 0
        if row is None:
            row = TenantQueueWeight(organization_id=org)
            db.add(row)
        row.weight, row.max_inflight, row.note, row.updated_by = args.weight, args.max_inflight, args.note, getpass.getuser()
        db.commit()
        print(f"{org}: weight={row.weight} max_inflight={row.max_inflight}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

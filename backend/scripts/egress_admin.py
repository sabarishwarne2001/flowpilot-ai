#!/usr/bin/env python3
"""ARCH50-S1:egress-admin — the operator's view of an organization's egress lockdown, and its one escape hatch.

    python scripts/egress_admin.py show --organization <id or slug>
    python scripts/egress_admin.py refusals [--organization <id or slug>] [--days 7]
    python scripts/egress_admin.py clear --organization <id or slug> --reason "left Enterprise; customer asked"

A LOCKDOWN OUTLIVES A DOWNGRADE (app/services/sovereign/egress_policy.py): editing the policy needs
`capability.egress_lockdown`, enforcing it does not, so an organization that switched the lockdown on and then left
Enterprise cannot switch it off from the console. `clear` is how the operator does it on the customer's word: the
lockdown goes off, the allow rules stay (switching it back on restores them), and the audit log records
ORGANIZATION / UPDATED `egress.lockdown_cleared_by_operator` with the reason. Nothing here makes a network call.
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from pathlib import Path
from typing import Any

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))


def resolve_organization(db: Any, ref: str) -> uuid.UUID:
    from sqlalchemy import text

    try:
        return uuid.UUID(str(ref))
    except ValueError:
        row = db.execute(text("SELECT id FROM organizations WHERE slug = :s"), {"s": str(ref)}).first()
        if row is None:
            raise SystemExit(f"no organization with slug {ref!r}")
        return row[0]


def _json(value: Any) -> str:
    return json.dumps(value, indent=2, default=str)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    show = sub.add_parser("show", help="an organization's lockdown and allow rules")
    show.add_argument("--organization", required=True)
    ref = sub.add_parser("refusals", help="what the gate refused (one organization, or every one)")
    ref.add_argument("--organization")
    ref.add_argument("--days", type=int, default=7)
    clear = sub.add_parser("clear", help="lift an organization's lockdown (audited; the rules stay)")
    clear.add_argument("--organization", required=True)
    clear.add_argument("--reason", required=True)
    args = parser.parse_args(argv)

    from app.db.session import SessionLocal
    from app.services.sovereign import egress_policy

    with SessionLocal() as db:
        if args.command == "show":
            print(_json(egress_policy.get_policy(db, organization_id=resolve_organization(db, args.organization))))
            return 0
        if args.command == "refusals":
            org = resolve_organization(db, args.organization) if args.organization else None
            print(_json(egress_policy.list_refusals(db, organization_id=org, days=args.days)))
            return 0
        org = resolve_organization(db, args.organization)
        try:
            policy = egress_policy.operator_clear(db, organization_id=org, reason=args.reason)
        except egress_policy.EgressPolicyError as exc:
            db.rollback()
            print(f"refused: {exc.code}: {exc}", file=sys.stderr)
            return 2
        db.commit()
        print(_json({"organization_id": org, "lockdown_enabled": policy["lockdown_enabled"],
                     "rules_kept": len(policy["rules"])}))
        return 0


if __name__ == "__main__":
    sys.exit(main())

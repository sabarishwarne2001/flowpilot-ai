"""Tenants and signed-in users for the load test (campaign session 4, §B.2; PERFORMANCE.md).

    python perf/load_seed.py --tenants 5 --users 10 --out /tmp/flowpilot-load-users.json

Creates (or refreshes) TENANTS Business organizations with one workspace each and USERS people in
each (an owner, an admin, the rest contributors), with the browser suite's own seeding code
(`scripts/seed_e2e.py`: published plans, a paid subscription, the seats they need). Then it opens
one real session per person, as a sign-in does, and writes their refresh tokens to OUT for
`perf/locustfile.py`: fifty people signing in from one machine at once would only measure the
sign-in rate limit.

For a test or staging database only: it refuses to run when ENVIRONMENT is production.
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.core.config import settings  # noqa: E402

if str(getattr(settings, "ENVIRONMENT", "")).strip().lower() == "production":
    raise SystemExit("load_seed.py refuses to run against a production deployment.")

from app.db.session import SessionLocal  # noqa: E402
from scripts import seed_e2e  # noqa: E402


def _tenants(count: int, users: int) -> tuple[seed_e2e.SeedTenant, ...]:
    out = []
    for n in range(1, count + 1):
        key = f"L{n}"
        people = [seed_e2e._user(key, "owner", "OWNER", "ADMIN", f"Load Owner {n}"),  # noqa: SLF001
                  seed_e2e._user(key, "admin", "ADMIN", "ADMIN", f"Load Admin {n}")]  # noqa: SLF001
        people += [seed_e2e._user(key, f"member{m}", "MEMBER", "CONTRIBUTOR", f"Load Member {n}.{m}")  # noqa: SLF001
                   for m in range(1, users - 1)]
        out.append(seed_e2e.SeedTenant(key=key, slug=f"load-tenant-{n}", name=f"Load Tenant {n}", plan="business",
                                       workspaces=(("main", f"Load {n} Main"),), users=tuple(people)))
    return tuple(out)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--tenants", type=int, default=5)
    parser.add_argument("--users", type=int, default=10, help="people per tenant (at least 3)")
    parser.add_argument("--out", default="/tmp/flowpilot-load-users.json")
    args = parser.parse_args(argv)

    from app.services.session_service import create_session

    seed_e2e.TENANTS = _tenants(args.tenants, max(3, args.users))
    seed_e2e._seed_organization_notices = lambda db: None  # noqa: SLF001 - the browser suite's fixtures
    seed_e2e._seed_dead_job = lambda db: None  # noqa: SLF001
    with SessionLocal() as db:
        summary = seed_e2e.seed(db)
        people = []
        for key, user in summary["users"].items():
            tenant = summary["tenants"][user["tenant"]]
            issued = create_session(db, user_id=uuid.UUID(user["user_id"]), ip_address="127.0.0.1", user_agent="locust")
            people.append({
                "key": key, "role": user["org_role"], "workspace_role": user["workspace_role"],
                "organization_id": tenant["organization_id"], "workspace_id": tenant["workspaces"][0]["id"],
                "refresh_token": issued.plaintext_token,
            })
        db.commit()
    Path(args.out).write_text(json.dumps({"users": people}, indent=1))
    print(json.dumps({"tenants": args.tenants, "users": len(people), "out": args.out}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

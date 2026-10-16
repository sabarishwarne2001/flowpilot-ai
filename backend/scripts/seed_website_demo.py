#!/usr/bin/env python3
"""The demonstration organization the website's screenshots are taken in (development only).

    python scripts/seed_website_demo.py

Creates (or repairs) one Enterprise organization, "Meridian Wholesale Pvt Ltd", with one owner,
`asha.rao@demo.example.com`, and the browser suite's password. Then
`website/scripts/capture-screenshots.mjs` signs in as that owner, builds Helm's sample company
(Meridian Wholesale, an invented Pune distributor, labelled "sample" everywhere) and photographs
the product. Nothing in it is a real customer, person or company.

It reuses `scripts/seed_e2e.py` (same plan, same test-mode subscription, nothing sent to a payment
provider) and refuses to run unless ENVIRONMENT is development or test.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts import seed_e2e  # noqa: E402

DEMO_EMAIL = "asha.rao@demo.example.com"
DEMO = seed_e2e.SeedTenant(
    key="W",
    slug="meridian-wholesale",
    name="Meridian Wholesale Pvt Ltd",
    plan="enterprise",
    workspaces=(("main", "Main"),),
    users=(
        seed_e2e.SeedUser(
            key="W.owner", email=DEMO_EMAIL, display_name="Asha Rao", org_role="OWNER", workspace_role="ADMIN"
        ),
    ),
)


def main() -> int:
    from app.core.config import settings
    from app.db.session import SessionLocal

    if (settings.ENVIRONMENT or "").strip().lower() not in {"development", "dev", "test", "testing", "local"}:
        print(f"seed_website_demo refuses ENVIRONMENT={settings.ENVIRONMENT!r}: its password is public.", file=sys.stderr)
        return 2
    with SessionLocal() as db:
        summary = seed_e2e.seed(db, tenants=(DEMO,))
    tenant = summary["tenants"]["W"]
    print(f"{tenant['name']} ({tenant['slug']}), plan {tenant['plan']}: sign in as {DEMO_EMAIL}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

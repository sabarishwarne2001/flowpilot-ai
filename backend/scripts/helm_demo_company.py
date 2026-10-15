"""Build Helm's sample company in an organization (brief §10.3).

    python scripts/helm_demo_company.py --org <organization slug> [--rebuild] [--print]

The same builder the "Explore Helm with a sample company" button runs (the `helm.sample` job):
Meridian Wholesale Pvt Ltd, an invented Pune distributor with nine months of documents, read by the
platform's own engines, in a labelled, deletable sample workspace with its own Helm book. `--rebuild`
archives an existing sample first; `--print` lists what was planted (the backtest finds exactly
that). Also the data the website's screenshots use. Refuses to run against production.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import asdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import settings  # noqa: E402

if str(settings.ENVIRONMENT).strip().lower() == "production":
    raise SystemExit("helm_demo_company.py refuses to run against a production deployment.")

from sqlalchemy import select  # noqa: E402

from app.db.session import SessionLocal  # noqa: E402
from app.models.organization import Organization  # noqa: E402
from app.services.helm import sample_company  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build Helm's sample company in an organization.")
    parser.add_argument("--org", required=True, help="organization slug")
    parser.add_argument("--rebuild", action="store_true", help="archive an existing sample first")
    parser.add_argument("--print", dest="show", action="store_true", help="print what was planted")
    args = parser.parse_args(argv)
    with SessionLocal() as db:
        organization = db.execute(select(Organization).where(Organization.slug == args.org)).scalar_one_or_none()
        if organization is None:
            raise SystemExit(f"No organization with the slug {args.org!r}.")
        if args.rebuild:
            sample_company.delete(db, organization_id=organization.id)
            db.commit()
        started = time.monotonic()
        book = sample_company.build(db, organization_id=organization.id, actor_user_id=None)
        db.commit()
        out = {"organization": args.org, "book_id": str(book.id), "workspace_id": str(book.workspace_id),
               "seconds": round(time.monotonic() - started, 1)}
        if args.show:
            _, planted = sample_company.plan()
            out["planted"] = asdict(planted)
        print(json.dumps(out, indent=2 if args.show else None))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

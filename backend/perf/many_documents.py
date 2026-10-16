"""A workspace with 10,000 documents: how fast its lists are (campaign session 4, §B.3).

    python perf/many_documents.py --org load-tenant-1 --count 10000 --api http://127.0.0.1:8000/api/v1

Adds COUNT processed documents to the organization's first workspace (rows only: no files, no
processing, which this is not measuring), then reads the document list as its owner through the
real API: the first page, a page near the end, a search and a status filter, five times each, and
prints the median and slowest time of each, and the database's plan for the first page.

For a test or staging database only: it refuses to run when ENVIRONMENT is production.
"""

from __future__ import annotations

import argparse
import statistics
import sys
import time
import uuid
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.core.config import settings  # noqa: E402

if str(getattr(settings, "ENVIRONMENT", "")).strip().lower() == "production":
    raise SystemExit("many_documents.py refuses to run against a production deployment.")

import httpx  # noqa: E402
from sqlalchemy import func, insert, select, text  # noqa: E402

from app.db.session import SessionLocal  # noqa: E402
from app.models.organization import Organization, OrganizationMember, OrganizationRole  # noqa: E402
from app.models.work_item import WorkItem  # noqa: E402
from app.models.workspace import Workspace  # noqa: E402
from app.services.session_service import create_session  # noqa: E402


def _seed(db, workspace_id: uuid.UUID, owner_id: uuid.UUID, count: int) -> int:
    have = db.execute(select(func.count()).select_from(WorkItem).where(WorkItem.workspace_id == workspace_id)).scalar_one()
    missing = max(0, count - have)
    rows = [{
        "id": uuid.uuid4(), "workspace_id": workspace_id, "created_by_user_id": owner_id,
        "original_filename": f"invoice-{n:05d}.pdf", "stored_filename": f"stored-{uuid.uuid4().hex}",
        "file_type": "application/pdf", "file_size": 20_000, "status": "COMPLETED", "pipeline_stage": "COMPLETED",
        "summary": f"Invoice {n} from Supplier {n % 97}",
    } for n in range(have, have + missing)]
    for start in range(0, len(rows), 1000):
        db.execute(insert(WorkItem), rows[start:start + 1000])
    db.commit()
    return missing


def _timed(client: httpx.Client, url: str, headers: dict[str, str], params: dict) -> tuple[float, float, int]:
    took = []
    for _ in range(5):
        started = time.perf_counter()
        response = client.get(url, headers=headers, params=params)
        took.append((time.perf_counter() - started) * 1000)
        response.raise_for_status()
    body = response.json()
    shown = len(body if isinstance(body, list) else body.get("items", []))
    return statistics.median(took), max(took), shown


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--org", required=True, help="organization slug")
    parser.add_argument("--count", type=int, default=10_000)
    parser.add_argument("--api", default="http://127.0.0.1:8000/api/v1")
    args = parser.parse_args(argv)

    with SessionLocal() as db:
        organization = db.execute(select(Organization).where(Organization.slug == args.org)).scalar_one()
        workspace = db.execute(select(Workspace).where(Workspace.organization_id == organization.id)
                               .order_by(Workspace.created_at)).scalars().first()
        owner = db.execute(select(OrganizationMember).where(OrganizationMember.organization_id == organization.id,
                                                            OrganizationMember.role == OrganizationRole.OWNER)).scalars().first()
        added = _seed(db, workspace.id, owner.user_id, args.count)
        total = db.execute(select(func.count()).select_from(WorkItem).where(WorkItem.workspace_id == workspace.id)).scalar_one()
        refresh = create_session(db, user_id=owner.user_id, ip_address="127.0.0.1", user_agent="perf").plaintext_token
        db.commit()
        plan = db.execute(text(
            "EXPLAIN (ANALYZE, BUFFERS) SELECT * FROM work_items WHERE workspace_id = :w "
            "ORDER BY created_at DESC, id DESC LIMIT 50"), {"w": workspace.id}).scalars().all()

    client = httpx.Client(timeout=60)
    access = client.post(f"{args.api}/auth/refresh", headers={"Cookie": f"servabo_refresh={refresh}"}).json()["access_token"]
    headers = {"Authorization": f"Bearer {access}"}
    url = f"{args.api}/workspaces/{workspace.id}/work-items"
    print(f"workspace {workspace.slug}: {total} documents ({added} added now)")
    print("| Read | Median ms | Slowest ms | Rows |")
    print("|---|---:|---:|---:|")
    for name, params in [("first page", {"limit": 50}), ("a page near the end", {"limit": 50, "skip": max(0, total - 60)}),
                         ("search by name", {"limit": 50, "search": "invoice-0999"}),
                         ("status filter", {"limit": 50, "status": "COMPLETED"})]:
        median, slowest, rows = _timed(client, url, headers, params)
        print(f"| {name} | {median:.0f} | {slowest:.0f} | {rows} |")
    print("\nPlan for the first page:")
    print("\n".join(f"    {line}" for line in plan))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

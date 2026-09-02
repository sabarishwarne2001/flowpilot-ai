#!/usr/bin/env python
"""ARCH-11 Step 4 — enqueue the knowledge backfill.

    python scripts/backfill_knowledge.py --dry-run
    python scripts/backfill_knowledge.py --workspace <uuid>
    python scripts/backfill_knowledge.py --all --batch 500
    python scripts/backfill_knowledge.py --status
"""

from __future__ import annotations

import argparse
import sys
import uuid
from pathlib import Path
from typing import Optional

# Enforce UTF-8 output streams across Windows CP1252 / Linux
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sqlalchemy import distinct, func, select  # noqa: E402

from app.db.session import SessionLocal  # noqa: E402
from app.models.document_chunk import DocumentChunk  # noqa: E402
from app.models.work_item import WorkItem  # noqa: E402
from app.models.workspace import Workspace  # noqa: E402
from app.services import job_service  # noqa: E402
from app.workers.handlers import register_all  # noqa: E402
from app.workers.handlers.knowledge_reindex import JOB_TYPE  # noqa: E402


def _candidates(db, *, workspace_id: Optional[uuid.UUID], limit: Optional[int]):
    statement = (
        select(WorkItem.id, WorkItem.workspace_id, Workspace.organization_id)
        .join(Workspace, Workspace.id == WorkItem.workspace_id)
        .where(WorkItem.extracted_text.isnot(None))
        .order_by(WorkItem.created_at.asc())
    )
    if workspace_id is not None:
        statement = statement.where(WorkItem.workspace_id == workspace_id)
    if limit:
        statement = statement.limit(limit)
    return db.execute(statement).all()


def _status(db) -> dict[str, int]:
    extracted = db.execute(
        select(func.count()).select_from(WorkItem).where(
            WorkItem.extracted_text.isnot(None)
        )
    ).scalar_one()
    indexed = db.execute(
        select(func.count(distinct(DocumentChunk.work_item_id)))
    ).scalar_one()
    chunks = db.execute(select(func.count()).select_from(DocumentChunk)).scalar_one()
    return {
        "documents_with_text": int(extracted),
        "documents_indexed": int(indexed),
        "documents_remaining": int(extracted) - int(indexed),
        "chunks": int(chunks),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", help="Workspace UUID. Omit with --all.")
    parser.add_argument("--all", action="store_true", help="Every workspace.")
    parser.add_argument("--batch", type=int, default=None, help="Cap the enqueue.")
    parser.add_argument("--status", action="store_true", help="Report and exit.")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    register_all(replace=True)

    with SessionLocal() as db:
        if args.status:
            for key, value in _status(db).items():
                print(f"[INFO] {key:<22} {value}")
            return 0

        if not args.all and not args.workspace:
            print(
                "[FAIL] pass --workspace <uuid> or --all. Backfilling every "
                "tenant is a deliberate act.",
                file=sys.stderr,
            )
            return 2

        workspace_id = uuid.UUID(args.workspace) if args.workspace else None
        rows = _candidates(db, workspace_id=workspace_id, limit=args.batch)
        print(f"[INFO] {len(rows)} document(s) eligible")

        if args.dry_run:
            for work_item_id, ws_id, _ in rows[:20]:
                print(f"[DRY ] would enqueue {JOB_TYPE} for {work_item_id} ({ws_id})")
            if len(rows) > 20:
                print(f"[DRY ] ... and {len(rows) - 20} more")
            return 0

        enqueued = 0
        for work_item_id, ws_id, organization_id in rows:
            job_service.enqueue(
                db,
                job_type=JOB_TYPE,
                payload={
                    "work_item_id": str(work_item_id),
                    "workspace_id": str(ws_id),
                },
                organization_id=organization_id,
                idempotency_key=f"{JOB_TYPE}:{work_item_id}",
            )
            enqueued += 1
        db.commit()

        print(f"[INFO] enqueued {enqueued} job(s)")
        print(
            "[INFO] run the enrich worker to drain them:\n"
            "       python -m app.worker --profile enrich"
        )
        print("[INFO] progress: python scripts/backfill_knowledge.py --status")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""ARCH44-S1:backfill-tables — extract tables from documents processed before ARCH-44.

    python scripts/backfill_tables.py                          # report what would be queued
    python scripts/backfill_tables.py --apply                  # queue tables.extract_document jobs
    python scripts/backfill_tables.py --apply --workspace <id> # one workspace only

New documents are scanned for tables after enrichment (post_enrichment). This
one-off command queues the documents that finished BEFORE the upgrade: every
completed PDF or image with no extracted tables yet, in organizations whose
plan carries capability.table_intelligence. The jobs run on the OCR worker
profile and are idempotent (the key carries the document id), so running the
command twice queues nothing new.
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))


def main() -> int:
    from sqlalchemy import exists, func, select

    from app.db.session import SessionLocal
    from app.models.tables import ExtractedTable
    from app.models.work_item import WorkItem
    from app.models.workspace import Workspace
    from app.services import job_service
    from app.services.tables import gate
    from app.services.tables import vocabulary as v

    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--workspace", default=None, help="limit to one workspace id")
    parser.add_argument("--limit", type=int, default=20000)
    args = parser.parse_args()
    mime = func.lower(func.split_part(WorkItem.file_type, ";", 1))
    query = (select(WorkItem.id, Workspace.organization_id)
             .join(Workspace, Workspace.id == WorkItem.workspace_id)
             .where(WorkItem.pipeline_stage == "COMPLETED", mime.in_(v.EXTRACTABLE_MIME),
                    ~exists().where(ExtractedTable.work_item_id == WorkItem.id))
             .order_by(WorkItem.created_at).limit(args.limit))
    if args.workspace:
        query = query.where(WorkItem.workspace_id == uuid.UUID(args.workspace))
    queued = skipped = 0
    with SessionLocal() as db:
        held: dict[uuid.UUID, bool] = {}
        for work_item_id, organization_id in db.execute(query).all():
            if organization_id not in held:
                held[organization_id] = gate.capability_held(db, organization_id)
            if not held[organization_id]:
                skipped += 1
                continue
            queued += 1
            if args.apply:
                job_service.enqueue(db, job_type=v.JOB_EXTRACT, organization_id=organization_id,
                                    payload={"work_item_id": str(work_item_id)},
                                    idempotency_key=f"{v.JOB_EXTRACT}:{work_item_id}:backfill")
        if args.apply:
            db.commit()
        else:
            db.rollback()
    print(json.dumps({"documents_to_queue": queued, "skipped_without_capability": skipped, "applied": args.apply,
                      "organizations": len(held)}))
    return 0


if __name__ == "__main__":
    sys.exit(main())

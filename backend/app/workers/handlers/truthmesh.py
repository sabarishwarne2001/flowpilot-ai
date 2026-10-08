"""TruthMesh's two jobs (LIGHT profile: SQL, stored embeddings and deterministic rules; no model).

`truthmesh.index_document`     after a document is enriched: its twin, its links, its neighbourhood's
                               conflicts (enqueued by post-enrichment dispatch).
`truthmesh.rebuild_workspace`  every document of a workspace (the cockpit's Rebuild, and a first build).

Both are idempotent: a re-run rewrites the same nodes, links and conflicts (conflicts keep their
identity by fingerprint). A workspace whose plan no longer carries capability.truthmesh is skipped.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

logger = logging.getLogger("app.workers.handlers.truthmesh")


def _workspace(db: Any, workspace_id: uuid.UUID) -> Any:
    from app.models.workspace import Workspace

    return db.get(Workspace, workspace_id)


def handle_index_document(payload: dict[str, Any]) -> dict[str, Any]:
    from app.db.session import SessionLocal
    from app.models.work_item import WorkItem
    from app.services.truthmesh import gate, service

    work_item_id = uuid.UUID(str(payload["work_item_id"]))
    with SessionLocal() as db:
        item = db.get(WorkItem, work_item_id)
        if item is None:
            return {"skipped": "document_not_found"}
        workspace = _workspace(db, item.workspace_id)
        if workspace is None or not gate.capability_held(db, workspace.organization_id):
            return {"skipped": "capability_not_held"}
        result = service.index_document(db, workspace_id=workspace.id, organization_id=workspace.organization_id,
                                        work_item_id=work_item_id)
        db.commit()
        logger.info("truthmesh.indexed", extra={"work_item_id": str(work_item_id), **result.as_dict()})
        return result.as_dict()


def handle_rebuild_workspace(payload: dict[str, Any]) -> dict[str, Any]:
    from app.db.session import SessionLocal
    from app.services.truthmesh import gate, service

    workspace_id = uuid.UUID(str(payload["workspace_id"]))
    with SessionLocal() as db:
        workspace = _workspace(db, workspace_id)
        if workspace is None or not gate.capability_held(db, workspace.organization_id):
            return {"skipped": "capability_not_held"}
        try:
            result = service.build_workspace(db, workspace_id=workspace.id, organization_id=workspace.organization_id)
            db.commit()
        except Exception as exc:
            db.rollback()
            # The cockpit shows the failure instead of "Building" forever; the job still fails and retries.
            service._refresh_state(db, workspace_id=workspace.id, organization_id=workspace.organization_id,
                                   ms=None, status="FAILED", error=f"{type(exc).__name__}: {str(exc)[:300]}")
            db.commit()
            raise
        logger.info("truthmesh.rebuilt", extra={"workspace_id": str(workspace_id), **result.as_dict()})
        return result.as_dict()


__all__ = ["handle_index_document", "handle_rebuild_workspace"]

"""Where the rest of the product tells Helm a document changed.

Called from: post-enrichment dispatch (a document was processed), the verification outcome and its
human resolution (verified), field corrections (corrected) and document deletion (deleted). Each
call asks for a debounced rebuild of that workspace's Promise ledger (`ledger_store.enqueue_rebuild`),
and only when some Helm book reads the workspace. It never raises into the caller: a document path
must not fail because Helm could not be told; the nightly full rebuild catches anything missed.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, Optional

logger = logging.getLogger("app.services.helm.hooks")


def document_changed(db: Any, *, workspace_id: Optional[uuid.UUID], organization_id: Optional[uuid.UUID] = None,
                     reason: str) -> None:
    if workspace_id is None:
        return
    try:
        if organization_id is None:
            from app.models.workspace import Workspace

            workspace = db.get(Workspace, workspace_id)
            if workspace is None:
                return
            organization_id = workspace.organization_id
        from app.services.helm import ledger_store

        with db.begin_nested():
            ledger_store.enqueue_rebuild(db, workspace_id=workspace_id, organization_id=organization_id,
                                         reason=reason)
    except Exception:  # noqa: BLE001
        logger.exception("helm.hook_failed", extra={"workspace_id": str(workspace_id), "reason": reason})


__all__ = ["document_changed"]

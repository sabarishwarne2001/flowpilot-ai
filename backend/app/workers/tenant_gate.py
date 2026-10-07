"""F-134 — no background work for an archived (or suspended) organization or workspace.

Archiving refuses every request into a tenant, but the worker kept going: queued
automations, ERP deliveries, warehouse exports, re-indexing and the document
engines ran for it as for anyone. The workspace picker tells the owner that an
archived organization's "automations and integrations are off"; this is what
makes that true.

Two mechanisms, because jobs come in two shapes:

* A job ABOUT ONE TENANT (its payload names a workspace, a document, or the job
  row names an organization) is wrapped by `gated()` at registration. Before the
  handler runs, the tenant is looked up; if it is not ACTIVE the job SUCCEEDS
  with `{"outcome": "SKIPPED", "reason": ...}`. Succeeding, not failing: retrying
  would only skip again, and a dead letter would page someone over a decision a
  customer made on purpose.
* A SWEEP across tenants (`anomaly.nightly`, the procurement re-score, the
  warehouse schedule dispatcher) filters archived tenants out of its own query.

Only TENANT_ACTIVITY_JOB_TYPES are gated. Billing, usage, compliance (erasure,
retention), identity and platform housekeeping keep running for an archived
organization: an archived tenant is still invoiced for its last period, still
has its data erased on request, and still has its storage measured. The
document pipeline itself (`document.extract`, `document.enrich`) is not gated
either: archiving refuses new uploads, and a document already accepted is
finished rather than left half-processed; the work it would fan out to the
gated engines is skipped.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, Callable, Optional

logger = logging.getLogger("app.workers.tenant_gate")

Handler = Callable[[dict[str, Any]], Optional[dict[str, Any]]]

TENANT_ACTIVITY_JOB_TYPES: frozenset[str] = frozenset(
    {
        # Customer-facing integrations and automations.
        "automation.execute",
        "erp.deliver_posting",
        "analytics.export_sync",
        # The engines a document fans out to, and on-demand engine work.
        "document.verify",
        "knowledge.reindex",
        "anomaly.scan_document",
        "anomaly.nightly",
        "entities.resolve_document",
        "tables.extract_document",
        "obligations.extract_document",
        "packets.detect_boundaries",
        "packets.apply_split",
        "cases.assemble_document",
        "corroboration.run",
        "procurement.score",
        "process.sweep_workspace",
        "redaction.detect",
        "redaction.apply",
        "work_items.bulk",
        "batch.expand_archive",
    }
)

_WORK_ITEM_KEYS = ("work_item_id", "invoice_work_item_id", "source_work_item_id")


def _uuid(value: Any) -> Optional[uuid.UUID]:
    if not value:
        return None
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError):
        return None


def inactive_reason(
    db: Any,
    *,
    organization_id: Optional[uuid.UUID] = None,
    workspace_id: Optional[uuid.UUID] = None,
    work_item_id: Optional[uuid.UUID] = None,
) -> Optional[str]:
    """Why this tenant may not run background work right now, or None if it may."""
    from app.models.organization import Organization, OrganizationStatus
    from app.models.work_item import WorkItem
    from app.models.workspace import Workspace, WorkspaceStatus

    if workspace_id is None and work_item_id is not None:
        item = db.get(WorkItem, work_item_id)
        workspace_id = item.workspace_id if item is not None else None

    if workspace_id is not None:
        workspace = db.get(Workspace, workspace_id)
        if workspace is not None:
            if workspace.status is not WorkspaceStatus.ACTIVE:
                return f"workspace {workspace.status.value.lower()}"
            organization_id = organization_id or workspace.organization_id

    if organization_id is not None:
        organization = db.get(Organization, organization_id)
        if organization is not None and organization.status is not OrganizationStatus.ACTIVE:
            return f"organization {organization.status.value.lower()}"
    return None


def _reason_for(payload: dict[str, Any]) -> Optional[str]:
    from app.db import session as session_module
    from app.models.job import Job

    organization_id = _uuid(payload.get("organization_id"))
    workspace_id = _uuid(payload.get("workspace_id"))
    work_item_id = next(
        (found for found in (_uuid(payload.get(key)) for key in _WORK_ITEM_KEYS) if found),
        None,
    )
    with session_module.SessionLocal() as db:
        if organization_id is None:
            job_id = _uuid(payload.get("job_id"))
            job = db.get(Job, job_id) if job_id else None
            organization_id = job.organization_id if job is not None else None
        if organization_id is None and workspace_id is None and work_item_id is None:
            return None  # a sweep across tenants: it filters archived tenants itself
        return inactive_reason(
            db,
            organization_id=organization_id,
            workspace_id=workspace_id,
            work_item_id=work_item_id,
        )


def gated(job_type: str, handler: Handler) -> Handler:
    """`handler`, but a job for a tenant that is not ACTIVE is skipped (and succeeds)."""

    def run(payload: dict[str, Any]) -> Optional[dict[str, Any]]:
        reason = _reason_for(payload or {})
        if reason is not None:
            logger.info(
                "jobs.skipped_inactive_tenant",
                extra={"job_type": job_type, "job_id": (payload or {}).get("job_id"), "reason": reason},
            )
            return {"outcome": "SKIPPED", "reason": reason}
        return handler(payload)

    run.__name__ = getattr(handler, "__name__", "handler")
    run.__qualname__ = getattr(handler, "__qualname__", run.__name__)
    run.__doc__ = handler.__doc__
    run.__wrapped__ = handler  # type: ignore[attr-defined]
    return run


__all__ = ["TENANT_ACTIVITY_JOB_TYPES", "gated", "inactive_reason"]

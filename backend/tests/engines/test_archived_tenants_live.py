"""F-134 — an archived organization or workspace does no background work.

Archiving refused every request into the tenant, but the worker kept going:
the nightly anomaly sweep and the procurement re-score walked every workspace
(both emit billable usage), scheduled warehouse exports kept pushing the
archived organization's data to its warehouse, and queued jobs (automations,
ERP deliveries, re-indexing, ...) ran for it as for anyone. The picker tells the
owner "its automations and integrations are off"; this makes that true.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

from app.models.document_role import DocumentRole
from app.models.organization import Organization, OrganizationStatus
from app.models.workspace import Workspace, WorkspaceStatus
from app.services import job_service
from tests.conftest import TestSessionLocal
from tests.engines.conftest import Engines, drain

PAGE = [
    "REFUND POLICY HANDBOOK",
    "Customers may return goods within thirty days of delivery for a full refund.",
]


def _archive_workspace(engines: Engines) -> None:
    spare = Workspace(
        organization_id=engines.org,
        slug=f"spare-{uuid.uuid4().hex[:6]}",
        workspace_name="Spare",
        status=WorkspaceStatus.ACTIVE,
    )
    engines.db.add(spare)
    engines.db.commit()
    response = engines.client.post(f"/api/v1/workspaces/{engines.ws}/archive", headers=engines.tenant.owner.headers)
    assert response.status_code == 200, response.text


def _archive_organization(engines: Engines) -> None:
    response = engines.client.post(
        f"/api/v1/organizations/{engines.org}/archive",
        json={"confirm_slug": engines.tenant.organization.slug},
        headers=engines.tenant.owner.headers,
    )
    assert response.status_code == 200, response.text


def _enqueue_reindex(engines: Engines, work_item_id: uuid.UUID, *, with_workspace: bool) -> None:
    payload = {"work_item_id": str(work_item_id)}
    if with_workspace:
        payload["workspace_id"] = str(engines.ws)
    with TestSessionLocal() as db, db.begin():
        job_service.enqueue(
            db,
            job_type="knowledge.reindex",
            organization_id=engines.org,
            payload=payload,
            idempotency_key=f"test:{uuid.uuid4().hex}",
        )


def test_a_queued_job_for_an_archived_workspace_is_skipped(engines: Engines) -> None:
    work_item_id = engines.process("refunds.pdf", [PAGE])
    _archive_workspace(engines)
    _enqueue_reindex(engines, work_item_id, with_workspace=True)

    runs = drain(only=["knowledge.reindex"])
    assert [run.ok for run in runs] == [True]
    assert runs[0].result["outcome"] == "SKIPPED", runs[0].result
    assert "archived" in runs[0].result["reason"]


def test_a_queued_job_for_an_archived_organization_is_skipped(engines: Engines) -> None:
    work_item_id = engines.process("refunds.pdf", [PAGE])
    _archive_organization(engines)
    # No workspace in the payload: the gate finds it from the document.
    _enqueue_reindex(engines, work_item_id, with_workspace=False)

    runs = drain(only=["knowledge.reindex"])
    assert [run.ok for run in runs] == [True]
    assert runs[0].result["outcome"] == "SKIPPED", runs[0].result


def test_the_nightly_anomaly_sweep_leaves_archived_workspaces_alone(engines: Engines) -> None:
    _archive_workspace(engines)
    with TestSessionLocal() as db, db.begin():
        job_service.enqueue(db, job_type="anomaly.nightly", payload={}, idempotency_key=f"test:{uuid.uuid4().hex}")
        live = db.execute(
            select(func.count())
            .select_from(Workspace)
            .join(Organization, Organization.id == Workspace.organization_id)
            .where(Workspace.status == WorkspaceStatus.ACTIVE, Organization.status == OrganizationStatus.ACTIVE)
        ).scalar_one()

    runs = drain(only=["anomaly.nightly"])
    assert [run.ok for run in runs] == [True]
    assert runs[0].result["workspaces"] == live, runs[0].result


def test_the_procurement_sweep_leaves_archived_workspaces_alone(engines: Engines) -> None:
    from app.workers.handlers.procurement import _sweep_targets

    marker = f"INV-ARC-{uuid.uuid4().hex[:6].upper()}"
    engines.process(
        f"{marker}.pdf",
        [["ACME SUPPLIES LTD", "TAX INVOICE", f"Invoice Number: {marker}", "PO Number: PO-ARC-1", "Total: 10.00 USD"]],
        marker=marker,
        classification="Invoice",
        entities={"vendor_name": "Acme Supplies Ltd", "invoice_number": marker, "po_number": "PO-ARC-1",
                  "currency": "USD", "total_amount": "10.00"},
    )
    engines.refresh()
    roles = engines.db.execute(select(DocumentRole).where(DocumentRole.workspace_id == engines.ws)).scalars().all()
    assert roles, "the invoice was not given a document role"

    _archive_workspace(engines)
    with TestSessionLocal() as db:
        swept = {role.workspace_id for role in _sweep_targets(db, workspace_id=None)}
    assert engines.ws not in swept


def test_scheduled_warehouse_exports_skip_an_archived_organization(engines: Engines) -> None:
    from app.services.analytics import sync_service
    from app.schemas.warehouse_sync import S3Credential

    destination = sync_service.create_destination(
        engines.db,
        organization_id=engines.org,
        label=f"warehouse-{uuid.uuid4().hex[:6]}",
        credential=S3Credential(
            bucket="acme-analytics", region="eu-west-1", prefix="flowpilot/",
            access_key_id="AKIAEXAMPLE0000000000", secret_access_key="s" * 40,
        ),
        actor_id=engines.tenant.owner.user.id,
    )
    schedule = sync_service.create_schedule(
        engines.db,
        organization_id=engines.org,
        destination_id=destination.id,
        datasets=["USAGE_ROLLUPS"],
        cadence="DAILY",
        hour_utc=2,
        day_of_week=None,
        day_of_month=None,
        lookback_days=1,
        enabled=True,
    )
    schedule.next_run_at = datetime.now(timezone.utc) - timedelta(minutes=5)
    engines.db.commit()
    assert schedule.id in {row.id for row in sync_service.due_schedules(engines.db)}

    _archive_organization(engines)
    engines.refresh()
    assert schedule.id not in {row.id for row in sync_service.due_schedules(engines.db)}

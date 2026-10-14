"""TruthMesh hardening: tenant isolation, roles, plans, concurrency and the audit trail, live.

    pytest tests/engines/test_truthmesh_hardening_live.py -q

`tests/engines/test_truthmesh_live.py` drives real PDFs through the pipeline into the mesh. These
tests need many meshes, two builds at once and two tenants, so they seed the documents the way
extraction leaves them (status COMPLETED, the recorded entities and text) and build with the same
service the rebuild job runs. The documents are the same purchase order, receipt, invoices, scan
and master agreement as the pipeline test, so the mesh holds the same links and conflicts.
"""

from __future__ import annotations

import threading
import uuid
from typing import Any

import pytest
from sqlalchemy import select

from app.models.truthmesh import MeshConflict, MeshLink, MeshNode, MeshState
from app.models.work_item import WorkItem
from tests.conftest import Fixture, TestSessionLocal
from tests.security.plans import put_on_plan

API = "/api/v1"
VENDOR = "Acme Industrial Supplies Ltd"
BUYER = "Caretakers Global Inc"


def _invoice(number: str, date: str, account: str) -> dict:
    return {"vendor_name": VENDOR, "customer_name": BUYER, "invoice_number": number, "po_number": "PO-TM-5001",
            "date": date, "currency": "USD", "subtotal": "1100.00", "tax_amount": "150.00",
            "total_amount": "1250.00", "payment_terms": "Net 30", "vendor_bank_account": account}


DOCUMENTS: list[tuple[str, str, str, list[str], dict]] = [
    ("contract-MSA-TM-2026.pdf", "MSA-TM-2026", "Contract",
     ["MASTER SERVICES AGREEMENT", "Agreement Number: MSA-TM-2026",
      f"Between {BUYER} (Customer) and {VENDOR} (Supplier)",
      "Effective Date: 2026-01-01. Renewal Date: 2026-12-31.",
      "1. Payment. Customer shall pay each undisputed invoice within 30 days of receipt.",
      "4. Termination. Either party may terminate with 60 days written notice before 2026-11-01."],
     {"party_names": [BUYER, VENDOR], "agreement_number": "MSA-TM-2026", "agreement_date": "2026-01-01",
      "termination_date": "2026-12-31", "governing_law": "England and Wales"}),
    ("purchase-order-PO-TM-5001.pdf", "PO-TM-5001", "Purchase Order",
     ["PURCHASE ORDER", f"Buyer: {BUYER}", f"Vendor: {VENDOR}", "PO Number: PO-TM-5001", "PO Total: 1100.00 USD"],
     {"vendor_name": VENDOR, "customer_name": BUYER, "po_number": "PO-TM-5001", "date": "2026-09-01",
      "currency": "USD", "total_amount": "1100.00"}),
    ("goods-receipt-GR-TM-7001.pdf", "GR-TM-7001", "Goods Receipt",
     ["GOODS RECEIPT NOTE", f"Vendor: {VENDOR}", "Receipt Number: GR-TM-7001", "PO Number: PO-TM-5001"],
     {"vendor_name": VENDOR, "customer_name": BUYER, "receipt_number": "GR-TM-7001", "po_number": "PO-TM-5001",
      "date": "2026-09-12"}),
    ("invoice-INV-TM-1001.pdf", "INV-TM-1001", "Invoice",
     ["INVOICE", f"Vendor: {VENDOR}", "Invoice Number: INV-TM-1001", "PO Number: PO-TM-5001"],
     _invoice("INV-TM-1001", "2026-09-15", "GB29 NWBK 6016 1331 9268 19")),
    ("invoice-INV-TM-1002.pdf", "INV-TM-1002", "Invoice",
     ["INVOICE", f"Vendor: {VENDOR}", "Invoice Number: INV-TM-1002", "PO Number: PO-TM-5001",
      "Note: our bank details have changed, please pay the new account."],
     _invoice("INV-TM-1002", "2026-09-20", "GB94 BARC 1020 1530 0934 59")),
    ("scan-of-INV-TM-1001.pdf", "SCAN-TM-1001", "Invoice",
     ["INVOICE (scanned copy)", "SCAN-TM-1001", f"Vendor: {VENDOR}", "Invoice Number: INV-TM-1001",
      "PO Number: PO-TM-5001"],
     _invoice("INV-TM-1001", "2026-09-15", "GB29 NWBK 6016 1331 9268 19")),
]


def seed_documents(db, workspace_id: uuid.UUID, user_id: uuid.UUID, *, tag: str = "") -> dict[str, uuid.UUID]:
    """The documents as extraction leaves them: COMPLETED, with their entities and text."""
    ids: dict[str, uuid.UUID] = {}
    for filename, marker, classification, lines, entities in DOCUMENTS:
        item = WorkItem(
            workspace_id=workspace_id, created_by_user_id=user_id, original_filename=f"{tag}{filename}",
            stored_filename=f"seed_{uuid.uuid4().hex}.pdf", file_type="application/pdf", file_size=1024,
            status="COMPLETED", summary="Seeded.", extracted_text="\n".join(lines),
            extracted_entities={"document_classification": classification, **entities}, extraction_metadata={},
        )
        db.add(item)
        db.flush()
        ids[marker] = item.id
    db.commit()
    return ids


def build(workspace_id: uuid.UUID, organization_id: uuid.UUID) -> None:
    from app.services.truthmesh import service

    with TestSessionLocal() as db:
        service.build_workspace(db, workspace_id=workspace_id, organization_id=organization_id)
        db.commit()


def url(workspace_id: uuid.UUID, path: str) -> str:
    return f"{API}/workspaces/{workspace_id}/truthmesh{path}"


@pytest.fixture()
def mesh(db_session, tenant: Fixture, test_sessions) -> dict[str, uuid.UUID]:
    """An Enterprise tenant whose workspace holds a built mesh."""
    put_on_plan(db_session, tenant.organization, "enterprise")
    ids = seed_documents(db_session, tenant.workspace.id, tenant.owner.user.id)
    build(tenant.workspace.id, tenant.organization.id)
    db_session.expire_all()
    return ids


def _conflicts(client, tenant: Fixture, **params: Any) -> list[dict]:
    response = client.get(url(tenant.workspace.id, "/conflicts"), params={"limit": 200, **params},
                          headers=tenant.owner.headers)
    assert response.status_code == 200, response.text
    return response.json()["items"]


def test_the_seeded_mesh_holds_the_pipeline_tests_conflicts(client, tenant: Fixture, mesh) -> None:
    found = {(c["kind"], c["severity"]) for c in _conflicts(client, tenant)}
    assert ("PAYEE_ACCOUNT_CHANGED", "CRITICAL") in found, found
    assert ("CUMULATIVE_OVERRUN", "CRITICAL") in found, found


# --------------------------------------------------------------------------- concurrency


def test_two_builds_of_one_workspace_at_once_both_succeed(db_session, tenant: Fixture, test_sessions,
                                                          monkeypatch: pytest.MonkeyPatch) -> None:
    """Two rebuild jobs (two clicks, or a rebuild while documents are indexed) overlap on one workspace.

    Each reads the mesh, finds no node for a document and inserts one: the second insert hits
    uq_mesh_nodes_work_item, the job fails, and the handler marks the mesh FAILED although the
    other build succeeded. Builds of one workspace must take turns.
    """
    from app.services.truthmesh import loader
    from app.workers.handlers.truthmesh import handle_rebuild_workspace

    put_on_plan(db_session, tenant.organization, "enterprise")
    seed_documents(db_session, tenant.workspace.id, tenant.owner.user.id)
    workspace_id = tenant.workspace.id

    # Both builds read the documents before either writes a node.
    gate = threading.Barrier(2, timeout=2)
    original = loader.sources

    def sources(*args: Any, **kwargs: Any):
        rows = original(*args, **kwargs)
        try:
            gate.wait()
        except threading.BrokenBarrierError:
            pass  # the other build is waiting its turn: this one goes on alone
        return rows

    monkeypatch.setattr(loader, "sources", sources)
    errors: list[BaseException] = []

    def run() -> None:
        try:
            handle_rebuild_workspace({"workspace_id": str(workspace_id)})
        except BaseException as exc:  # noqa: BLE001 - the test reports it
            errors.append(exc)

    threads = [threading.Thread(target=run) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)

    assert not errors, [f"{type(e).__name__}: {str(e)[:200]}" for e in errors]
    db_session.expire_all()
    state = db_session.get(MeshState, workspace_id)
    assert state is not None and state.status == "READY", (state.status if state else None, state and state.error)
    nodes = db_session.execute(select(MeshNode).where(MeshNode.workspace_id == workspace_id)).scalars().all()
    assert len(nodes) == len(DOCUMENTS)

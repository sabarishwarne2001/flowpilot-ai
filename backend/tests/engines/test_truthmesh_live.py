"""TruthMesh, live: real documents through the real pipeline into the cross-document twin.

A purchase order, its goods receipt, two invoices against it (the second asks to be paid into a new
bank account and bills the same goods again), a second copy of the first invoice, and the master
services agreement between the two companies. After enrichment every document is indexed by
`truthmesh.index_document`, and the mesh must say what a controller would:

* the invoices BILL_AGAINST the PO, the receipt RECEIVES_AGAINST it, the copy is a VERSION_OF the
  first invoice, and the PO is ISSUED_UNDER the MSA (its parties are the MSA's parties);
* CRITICAL: INV-TM-1002 changes the payee account; the two invoices draw USD 2,200.00 on a USD
  1,100.00 order; HIGH: they bill the same amount twice; the copy is a duplicate;
* the money at risk counts each payment once (USD 2,500.00: the second invoice and the copy).

Then the lifecycle: a dismissal needs a reason and survives a rebuild; rejecting the second
invoice's link to the PO auto-resolves the conflicts that rested on it; simulations reach the
dependent documents; viewers cannot decide; a lower plan is refused; another workspace sees nothing.
"""

from __future__ import annotations

from sqlalchemy import select

from app.models.truthmesh import MeshConflict, MeshLink, MeshNode
from tests.engines.conftest import Engines, drain
from tests.engines.isolation import assert_workspace_isolated

VENDOR = "Acme Industrial Supplies Ltd"
BUYER = "Caretakers Global Inc"


def _invoice(number: str, date: str, account: str) -> dict:
    return {"vendor_name": VENDOR, "customer_name": BUYER, "invoice_number": number, "po_number": "PO-TM-5001",
            "date": date, "currency": "USD", "subtotal": "1100.00", "tax_amount": "150.00",
            "total_amount": "1250.00", "payment_terms": "Net 30", "vendor_bank_account": account}


def _load(engines: Engines) -> dict[str, object]:
    ids: dict[str, object] = {}
    docs = [
        ("contract-MSA-TM-2026.pdf", "MSA-TM-2026", "Contract",
         ["MASTER SERVICES AGREEMENT", "Agreement Number: MSA-TM-2026",
          f"Between {BUYER} (Customer) and {VENDOR} (Supplier)",
          "Effective Date: 2026-01-01. Renewal Date: 2026-12-31.",
          "1. Payment. Customer shall pay each undisputed invoice within 30 days of receipt.",
          "4. Termination. Either party may terminate with 60 days written notice before 2026-11-01.",
          "6. Governing law. This agreement is governed by the laws of England and Wales."],
         {"party_names": [BUYER, VENDOR], "agreement_number": "MSA-TM-2026", "agreement_date": "2026-01-01",
          "termination_date": "2026-12-31", "governing_law": "England and Wales"}),
        ("purchase-order-PO-TM-5001.pdf", "PO-TM-5001", "Purchase Order",
         ["PURCHASE ORDER", f"Buyer: {BUYER}", f"Vendor: {VENDOR}", "PO Number: PO-TM-5001", "PO Date: 2026-09-01",
          "PO Total: 1100.00 USD"],
         {"vendor_name": VENDOR, "customer_name": BUYER, "po_number": "PO-TM-5001", "date": "2026-09-01",
          "currency": "USD", "total_amount": "1100.00"}),
        ("goods-receipt-GR-TM-7001.pdf", "GR-TM-7001", "Goods Receipt",
         ["GOODS RECEIPT NOTE", f"Vendor: {VENDOR}", "Receipt Number: GR-TM-7001", "PO Number: PO-TM-5001",
          "Received Date: 2026-09-12"],
         {"vendor_name": VENDOR, "customer_name": BUYER, "receipt_number": "GR-TM-7001", "po_number": "PO-TM-5001",
          "date": "2026-09-12"}),
        ("invoice-INV-TM-1001.pdf", "INV-TM-1001", "Invoice",
         ["INVOICE", f"Vendor: {VENDOR}", "Invoice Number: INV-TM-1001", "PO Number: PO-TM-5001",
          "Total Amount Due: 1250.00 USD"],
         _invoice("INV-TM-1001", "2026-09-15", "GB29 NWBK 6016 1331 9268 19")),
        ("invoice-INV-TM-1002.pdf", "INV-TM-1002", "Invoice",
         ["INVOICE", f"Vendor: {VENDOR}", "Invoice Number: INV-TM-1002", "PO Number: PO-TM-5001",
          "Total Amount Due: 1250.00 USD", "Note: our bank details have changed, please pay the new account."],
         _invoice("INV-TM-1002", "2026-09-20", "GB94 BARC 1020 1530 0934 59")),
        ("scan-of-INV-TM-1001.pdf", "SCAN-TM-1001", "Invoice",
         ["INVOICE (scanned copy)", "SCAN-TM-1001", f"Vendor: {VENDOR}", "Invoice Number: INV-TM-1001",
          "PO Number: PO-TM-5001", "Total Amount Due: 1250.00 USD"],
         _invoice("INV-TM-1001", "2026-09-15", "GB29 NWBK 6016 1331 9268 19")),
    ]
    for filename, marker, kind, text, entities in docs:
        ids[marker] = engines.process(filename, [text], marker=marker, classification=kind, entities=entities)
    engines.refresh()
    return ids


def _conflicts(engines: Engines, **params) -> list[dict]:
    response = engines.get("/truthmesh/conflicts", params={"limit": 200, **params})
    assert response.status_code == 200, response.text
    return response.json()["items"]


def _link(engines: Engines, source, target) -> MeshLink:
    engines.refresh()
    return engines.db.execute(select(MeshLink).where(
        MeshLink.workspace_id == engines.ws,
        ((MeshLink.source_work_item_id == source) & (MeshLink.target_work_item_id == target))
        | ((MeshLink.source_work_item_id == target) & (MeshLink.target_work_item_id == source)))).scalar_one()


def test_the_mesh_links_documents_and_finds_their_conflicts(engines: Engines) -> None:
    ids = _load(engines)

    nodes = engines.db.execute(select(MeshNode).where(MeshNode.workspace_id == engines.ws)).scalars().all()
    assert {n.work_item_id for n in nodes} == set(ids.values())
    kinds = {n.work_item_id: n.kind for n in nodes}
    assert kinds[ids["MSA-TM-2026"]] == "MASTER_AGREEMENT"
    assert kinds[ids["PO-TM-5001"]] == "PURCHASE_ORDER"

    assert _link(engines, ids["INV-TM-1001"], ids["PO-TM-5001"]).relation == "BILLS_AGAINST"
    assert _link(engines, ids["INV-TM-1002"], ids["PO-TM-5001"]).relation == "BILLS_AGAINST"
    assert _link(engines, ids["GR-TM-7001"], ids["PO-TM-5001"]).relation == "RECEIVES_AGAINST"
    assert _link(engines, ids["SCAN-TM-1001"], ids["INV-TM-1001"]).relation == "VERSION_OF"
    po_msa = _link(engines, ids["PO-TM-5001"], ids["MSA-TM-2026"])
    assert po_msa.relation == "ISSUED_UNDER" and po_msa.source_work_item_id == ids["PO-TM-5001"]

    found = {(c["kind"], c["severity"]) for c in _conflicts(engines)}
    assert ("PAYEE_ACCOUNT_CHANGED", "CRITICAL") in found, found
    assert ("CUMULATIVE_OVERRUN", "CRITICAL") in found, found
    assert ("DUPLICATE_BILLING", "HIGH") in found, found
    assert ("DUPLICATE_BILLING", "MEDIUM") in found, found  # the scanned copy
    payee = [c for c in _conflicts(engines) if c["kind"] == "PAYEE_ACCOUNT_CHANGED"]
    assert len(payee) == 1, "one change of account is one conflict, however many copies it is seen against"
    assert "•••• 3459" in payee[0]["summary"] and "•••• 6819" in payee[0]["summary"]
    overrun = next(c for c in _conflicts(engines) if c["kind"] == "CUMULATIVE_OVERRUN")
    assert overrun["exposure_micros"] == 1_100_000_000 and overrun["currency"] == "USD"

    overview = engines.get("/truthmesh/overview").json()
    assert overview["documents"] == 6
    assert overview["by_severity"]["CRITICAL"] == 2
    assert overview["exposure_micros"] == 2_500_000_000, overview["exposure_micros"]
    assert overview["exposure_currency"] == "USD"
    assert overview["risk_index"] > 0
    assert overview["top_risks"][0]["work_item_id"] in {str(ids["INV-TM-1002"]), str(ids["INV-TM-1001"])}

    twin = engines.get(f"/truthmesh/documents/{ids['MSA-TM-2026']}").json()
    assert twin["terms"]["payment_days"]["value"] == 30
    assert twin["terms"]["notice_days"]["value"] == 60
    assert twin["node"]["end_date"] == "2026-12-31"
    matrix = engines.get("/truthmesh/matrix").json()
    assert matrix["rows"] and matrix["columns"]
    csv_body = engines.get("/truthmesh/conflicts/export.csv")
    assert csv_body.status_code == 200 and "Payee account changed" in csv_body.text

    assert_workspace_isolated(engines, collections=("truthmesh",))


def test_decisions_rebuilds_and_rejected_links(engines: Engines) -> None:
    ids = _load(engines)
    duplicate = next(c for c in _conflicts(engines) if c["kind"] == "DUPLICATE_BILLING" and c["severity"] == "MEDIUM")

    refused = engines.patch(f"/truthmesh/conflicts/{duplicate['id']}", {"status": "DISMISSED"})
    assert refused.status_code == 422, refused.text
    viewer = engines.patch(f"/truthmesh/conflicts/{duplicate['id']}", {"status": "DISMISSED", "note": "scan"},
                           as_user=engines.tenant.viewer)
    assert viewer.status_code == 403
    dismissed = engines.patch(f"/truthmesh/conflicts/{duplicate['id']}",
                              {"status": "DISMISSED", "note": "The scan is the paper copy of the same invoice."})
    assert dismissed.status_code == 200 and dismissed.json()["status"] == "DISMISSED"

    before = {c["id"]: c["kind"] for c in _conflicts(engines)}
    rebuild = engines.post("/truthmesh/rebuild")
    assert rebuild.status_code == 202, rebuild.text
    drain()
    after = {c["id"]: c["kind"] for c in _conflicts(engines)}
    assert after == before, "a rebuild keeps each conflict's identity"
    still = engines.get(f"/truthmesh/conflicts?status=DISMISSED").json()["items"]
    assert [c["id"] for c in still] == [duplicate["id"]]

    link = _link(engines, ids["INV-TM-1002"], ids["PO-TM-5001"])
    rejected = engines.post(f"/truthmesh/links/{link.id}/decision", {"decision": "REJECTED"})
    assert rejected.status_code == 200 and rejected.json()["status"] == "REJECTED"
    kinds = {c["kind"] for c in _conflicts(engines)}
    assert "CUMULATIVE_OVERRUN" not in kinds and "DUPLICATE_BILLING" not in {
        c["kind"] for c in _conflicts(engines) if c["severity"] == "HIGH"}
    resolved = engines.db.execute(select(MeshConflict).where(
        MeshConflict.workspace_id == engines.ws, MeshConflict.kind == "CUMULATIVE_OVERRUN")).scalar_one()
    engines.db.refresh(resolved)
    assert resolved.status == "RESOLVED" and resolved.auto_resolved
    # The rejection survives a rebuild: the engine does not re-create a link a person rejected.
    engines.post("/truthmesh/rebuild")
    drain()
    assert _link(engines, ids["INV-TM-1002"], ids["PO-TM-5001"]).status == "REJECTED"
    assert "CUMULATIVE_OVERRUN" not in {c["kind"] for c in _conflicts(engines)}


def test_what_if_ripples_through_dependent_documents(engines: Engines) -> None:
    ids = _load(engines)

    delay = engines.post("/truthmesh/simulations", {"work_item_id": str(ids["PO-TM-5001"]), "scenario": "DELAY",
                                                    "days": 14})
    assert delay.status_code == 201, delay.text
    body = delay.json()
    reached = {n["work_item_id"] for n in body["result"]["nodes"]}
    assert {str(ids["GR-TM-7001"]), str(ids["INV-TM-1001"]), str(ids["INV-TM-1002"])} <= reached
    assert body["documents_affected"] >= 4
    titles = " ".join(e["title"] for n in body["result"]["nodes"] for e in n["effects"])
    assert "held until the goods arrive" in titles and "slips 14 days" in titles

    terminated = engines.post("/truthmesh/simulations", {"work_item_id": str(ids["MSA-TM-2026"]),
                                                         "scenario": "TERMINATION"}).json()
    assert terminated["result"]["summary"]["financial_exposure_micros"] == 2_200_000_000
    assert terminated["result"]["clause"]["clause_type"] == "termination"

    clause = engines.post("/truthmesh/simulations", {"work_item_id": str(ids["MSA-TM-2026"]),
                                                     "scenario": "CLAUSE_INVOKED", "clause": "4"}).json()
    assert clause["result"]["clause"]["found"] and "60 days" in clause["result"]["clause"]["quote"]

    over = engines.post("/truthmesh/simulations", {"work_item_id": str(ids["INV-TM-1002"]),
                                                   "scenario": "AMOUNT_CHANGE", "percent": 8}).json()
    assert over["result"]["summary"]["breaches"] == 1
    assert over["result"]["summary"]["financial_exposure_micros"] == 88_000_000

    missing = engines.post("/truthmesh/simulations", {"work_item_id": str(ids["MSA-TM-2026"]),
                                                      "scenario": "CLAUSE_INVOKED"})
    assert missing.status_code == 422
    listed = engines.get("/truthmesh/simulations").json()
    assert len(listed) == 4
    one = engines.get(f"/truthmesh/simulations/{listed[0]['id']}")
    assert one.status_code == 200 and one.json()["result"]["nodes"]
    viewer = engines.post("/truthmesh/simulations", {"work_item_id": str(ids["PO-TM-5001"]), "scenario": "DELAY"},
                          as_user=engines.tenant.viewer)
    assert viewer.status_code == 403


def test_a_plan_without_truthmesh_is_refused_and_not_indexed(engines: Engines) -> None:
    engines.plan("business")
    engines.process("invoice-INV-TM-9001.pdf", [["INVOICE", "Invoice Number: INV-TM-9001"]], marker="INV-TM-9001",
                    classification="Invoice", entities={"vendor_name": VENDOR, "invoice_number": "INV-TM-9001"})
    engines.refresh()
    assert engines.db.execute(select(MeshNode).where(MeshNode.workspace_id == engines.ws)).first() is None
    refused = engines.get("/truthmesh/overview")
    assert refused.status_code == 402, refused.text

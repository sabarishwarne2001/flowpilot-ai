"""Case intelligence, live: multi-document bundles checked against a template's rules.

* A published template requires a purchase order and an invoice and checks:
  the vendor names match (fuzzy), the invoices add up to the PO total, and the
  PO is dated on or before the invoice.
* A consistent bundle becomes COMPLETE; a bundle whose invoice names another
  vendor becomes INCONSISTENT with that rule FAILED and its two values shown;
  a bundle missing its invoice stays INCOMPLETE (missing is not a failure).
* An ENTITY-anchored template assembles cases by itself: documents whose
  vendor resolves to one entity-graph record land in one case.
* The document type of a non-procurement document comes from the model's
  classification (enrichment writes it under `classification_details`).
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.models.outbox_event import OutboxEvent
from tests.engines.conftest import Engines

RULES = [
    {"id": "same-vendor", "op": "FUZZY_EQUAL", "label": "Invoice vendor matches the PO",
     "left": {"doc_type": "invoice", "field": "vendor_name"}, "right": {"doc_type": "purchase_order", "field": "vendor_name"}},
    {"id": "adds-up", "op": "SUM_EQUALS", "label": "Invoices add up to the PO",
     "terms": [{"doc_type": "invoice", "field": "total_amount"}], "right": {"doc_type": "purchase_order", "field": "total_amount"}},
    {"id": "po-first", "op": "DATE_ORDER", "label": "PO dated before the invoice",
     "left": {"doc_type": "purchase_order", "field": "date"}, "right": {"doc_type": "invoice", "field": "date"}},
]
REQUIRED = [{"doc_type": "purchase_order", "label": "Purchase order"}, {"doc_type": "invoice", "label": "Invoice"}]


def _template(engines: Engines, key: str, **extra) -> dict:
    created = engines.post("/case-templates", {"key": key, "name": key.title(), "required_documents": REQUIRED,
                                               "rules": RULES, **extra})
    assert created.status_code == 201, created.text
    published = engines.post(f"/case-templates/{created.json()['id']}/publish")
    assert published.status_code == 200, published.text
    return published.json()


def _po(engines: Engines, n: str, vendor: str, total: str) -> str:
    return str(engines.process(f"{n}.pdf", [[vendor.upper(), "PURCHASE ORDER", f"PO Number: {n}", "Order Date: 2026-08-01",
                                             f"Total: {total}"]], marker=n, classification="Purchase Order",
                               entities={"vendor_name": vendor, "po_number": n, "date": "2026-08-01",
                                         "total_amount": total}))


def _invoice(engines: Engines, n: str, vendor: str, total: str, po: str) -> str:
    return str(engines.process(f"{n}.pdf", [[vendor.upper(), "TAX INVOICE", f"Invoice Number: {n}", f"PO Number: {po}",
                                             "Invoice Date: 2026-08-15", f"Total: {total}"]], marker=n,
                               classification="Invoice",
                               entities={"vendor_name": vendor, "invoice_number": n, "po_number": po,
                                         "date": "2026-08-15", "total_amount": total}))


def _case(engines: Engines, template: dict, title: str, documents: list[str]) -> dict:
    case = engines.post("/cases", {"template_id": template["id"], "title": title})
    assert case.status_code == 201, case.text
    case_id = case.json()["case"]["id"]
    for work_item_id in documents:
        added = engines.post(f"/cases/{case_id}/documents", {"work_item_id": work_item_id})
        assert added.status_code == 200, added.text
    evaluated = engines.post(f"/cases/{case_id}/evaluate")
    assert evaluated.status_code == 200, evaluated.text
    return evaluated.json()


def test_consistent_inconsistent_and_incomplete_bundles(engines: Engines) -> None:
    template = _template(engines, "po-invoice")
    po = _po(engines, "PO-CASE-1", "Acme Supplies Ltd", "1000.00")
    good = _invoice(engines, "INV-CASE-1", "ACME SUPPLIES LIMITED", "1000.00", "PO-CASE-1")
    bad = _invoice(engines, "INV-CASE-2", "Globex Corporation", "1000.00", "PO-CASE-1")

    complete = _case(engines, template, "Consistent", [po, good])
    assert complete["case"]["status"] == "COMPLETE", complete["rules"]
    assert {r["rule_id"]: r["outcome"] for r in complete["rules"]} == {"same-vendor": "PASS", "adds-up": "PASS",
                                                                       "po-first": "PASS"}
    assert all(slot["satisfied"] for slot in complete["checklist"])

    inconsistent = _case(engines, template, "Wrong vendor", [po, bad])
    assert inconsistent["case"]["status"] == "INCONSISTENT"
    failed = {r["rule_id"]: r for r in inconsistent["rules"] if r["outcome"] == "FAIL"}
    assert set(failed) == {"same-vendor"}, inconsistent["rules"]
    assert "globex" in (failed["same-vendor"]["left_value"] or "").lower()
    assert inconsistent["case"]["failed_rules"] == 1

    incomplete = _case(engines, template, "No invoice yet", [po])
    assert incomplete["case"]["status"] == "INCOMPLETE"
    assert not any(r["outcome"] == "FAIL" for r in incomplete["rules"])

    engines.refresh()
    events = set(engines.db.execute(select(OutboxEvent.event_type).where(
        OutboxEvent.organization_id == engines.org)).scalars())
    assert {"trigger.case.completed", "trigger.case.inconsistent"} <= events, events

    assert engines.post(f"/cases/{complete['case']['id']}/evaluate", as_user=engines.tenant.viewer).status_code == 403
    listing = engines.get("/cases").json()
    assert listing["counts_by_status"].get("COMPLETE") == 1 and listing["counts_by_status"].get("INCONSISTENT") == 1


def test_an_entity_anchored_template_assembles_cases_by_itself(engines: Engines) -> None:
    _template(engines, "vendor-file", assembly_key="ENTITY", entity_kind="ORGANIZATION", entity_role="vendor")
    _po(engines, "PO-AUTO-1", "Initech Hardware", "450.00")
    _invoice(engines, "INV-AUTO-1", "Initech Hardware", "450.00", "PO-AUTO-1")

    cases = engines.get("/cases").json()["items"]
    initech = [c for c in cases if "initech" in c["title"].lower()]
    assert len(initech) == 1, cases
    assert initech[0]["documents"] == 2
    assert initech[0]["status"] == "COMPLETE", initech[0]


def test_a_documents_type_comes_from_the_models_classification(engines: Engines) -> None:
    """No procurement role and no telling words on the page: the model's reading decides."""
    from app.models.work_item import WorkItem
    from app.services.cases import doc_types

    work_item_id = engines.process("cv.pdf", [["JANE DOE", "Reference REF-CV-9", "Pune, India"]], marker="REF-CV-9",
                                   classification="Curriculum Vitae", entities={"candidate_name": "Jane Doe"})
    engines.refresh()
    item = engines.db.get(WorkItem, work_item_id)
    assert doc_types.document_type_of(engines.db, item) == "resume"

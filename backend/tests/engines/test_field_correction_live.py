"""N-020 items 6/7 — the document viewer: its text, and correcting an extracted field in place.

Live through the real pipeline (upload -> extract -> enrich -> verification):

* a contributor corrects a wrong invoice number; the record, the correction
  history, the audit log and the `work_item.updated` event all follow;
* a viewer may read the text and the history but not correct;
* a document still in the review queue, or under a legal hold, is refused;
* a table / nested value cannot be overwritten with text;
* on a verified document the correction teaches extraction memory, exactly as
  a review would (an exemplar labelled CORRECTED for the layout).
"""

from __future__ import annotations

import uuid

from sqlalchemy import select

from app.models.audit_log import AuditLog
from app.models.extraction_memory import ExtractionExemplar
from app.models.outbox_event import OutboxEvent
from app.services.ingestion import retention_service
from tests.engines.conftest import Engines

TEXT = ["ACME SUPPLIES LTD", "TAX INVOICE", "Invoice No: INV-FIX-1", "PO Reference: PO-551", "Total: 120.00 INR"]
TRUTH = {"vendor_name": "Acme Supplies Ltd", "invoice_number": "INV-FIX-1", "total_amount": "120.00",
         "currency": "INR", "po_number": "PO-551"}
WRONG = {**TRUTH, "invoice_number": "PO-551", "line_items": [{"description": "Bolts", "amount": "120.00"}]}


def _invoice(engines: Engines, entities=WRONG, agents=None) -> uuid.UUID:
    return engines.process("fix.pdf", [TEXT], marker="INV-FIX-1", classification="Invoice", entities=entities,
                           agents=agents)


def test_a_contributor_corrects_a_field_and_everything_records_it(engines: Engines) -> None:
    work_item_id = _invoice(engines)

    response = engines.patch(
        f"/work-items/{work_item_id}/fields",
        {"corrections": {"invoice_number": " INV-FIX-1 ", "total_amount": "120.00"}, "reason": "Model read the PO"},
        as_user=engines.tenant.contributor,
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["extracted_entities"]["invoice_number"] == "INV-FIX-1"
    # total_amount was already right: only the changed field is a correction.
    assert [row["field_path"] for row in body["corrected"]] == ["invoice_number"]
    assert body["corrected"][0]["previous_value"] == "PO-551"
    assert body["corrected"][0]["reason"] == "Model read the PO"
    assert engines.item(work_item_id).extracted_entities["invoice_number"] == "INV-FIX-1"

    history = engines.get(f"/work-items/{work_item_id}/fields/history", as_user=engines.tenant.viewer)
    assert history.status_code == 200
    assert [(h["field_path"], h["previous_value"], h["corrected_value"]) for h in history.json()] == [
        ("invoice_number", "PO-551", "INV-FIX-1")
    ]

    engines.refresh()
    audit = engines.db.execute(
        select(AuditLog).where(AuditLog.resource_id == work_item_id, AuditLog.resource_type == "WORK_ITEM")
    ).scalars().all()
    assert len(audit) == 1 and audit[0].details["fields_corrected"] == ["invoice_number"]
    assert "INV-FIX-1" not in str(audit[0].details)  # values stay out of the audit log
    events = engines.db.execute(
        select(OutboxEvent).where(OutboxEvent.event_type == "work_item.updated", OutboxEvent.resource_id == work_item_id)
    ).scalars().all()
    assert len(events) == 1 and events[0].payload["fields_corrected"] == ["invoice_number"]

    text = engines.get(f"/work-items/{work_item_id}/text", as_user=engines.tenant.viewer)
    assert text.status_code == 200 and "Invoice No: INV-FIX-1" in text.json()["text"]


def test_a_viewer_cannot_correct(engines: Engines) -> None:
    work_item_id = _invoice(engines)
    response = engines.patch(f"/work-items/{work_item_id}/fields", {"corrections": {"invoice_number": "X"}},
                             as_user=engines.tenant.viewer)
    assert response.status_code == 403, response.text
    assert engines.item(work_item_id).extracted_entities["invoice_number"] == "PO-551"


def test_a_table_value_and_a_bad_field_name_are_refused(engines: Engines) -> None:
    work_item_id = _invoice(engines)
    table = engines.patch(f"/work-items/{work_item_id}/fields", {"corrections": {"line_items": "none"}})
    assert table.status_code == 422 and table.json()["detail"]["code"] == "FIELD_NOT_PLAIN", table.text
    nested = engines.patch(f"/work-items/{work_item_id}/fields", {"corrections": {"vendor_name": {"a": 1}}})
    assert nested.status_code == 422 and nested.json()["detail"]["code"] == "VALUE_NOT_PLAIN", nested.text
    bad = engines.patch(f"/work-items/{work_item_id}/fields", {"corrections": {"$where": "1"}})
    assert bad.status_code == 422 and bad.json()["detail"]["code"] == "INVALID_FIELD", bad.text
    assert engines.item(work_item_id).extracted_entities["line_items"] == WRONG["line_items"]


def test_a_document_in_the_review_queue_is_corrected_there(engines: Engines) -> None:
    engines.plan("business")
    assert engines.put("/document-settings/", {"verification_enabled": True, "verification_agents": 2}).status_code == 200
    work_item_id = _invoice(engines, agents=[WRONG, TRUTH])

    response = engines.patch(f"/work-items/{work_item_id}/fields", {"corrections": {"invoice_number": "INV-FIX-1"}})

    assert response.status_code == 409 and response.json()["detail"]["code"] == "REVIEW_PENDING", response.text


def test_a_document_under_legal_hold_is_frozen(engines: Engines) -> None:
    work_item_id = _invoice(engines)
    retention_service.place_hold(engines.db, organization_id=engines.org, workspace_id=None,
                                 work_item_id=work_item_id, reason="Audit 2026", reference=None,
                                 user_id=engines.tenant.owner.user.id)
    engines.refresh()

    response = engines.patch(f"/work-items/{work_item_id}/fields", {"corrections": {"invoice_number": "INV-FIX-1"}})

    assert response.status_code == 409 and response.json()["detail"]["code"] == "RETENTION_HOLD", response.text
    assert engines.item(work_item_id).extracted_entities["invoice_number"] == "PO-551"


def test_on_a_verified_document_the_correction_teaches_extraction_memory(engines: Engines) -> None:
    engines.plan("business")
    assert engines.put("/document-settings/", {"verification_enabled": True}).status_code == 200
    assert engines.put("/extraction-memory/settings", {"mode": "SHADOW"}).status_code == 200
    # Every agent agrees on the wrong value: auto-approved, never reviewed.
    work_item_id = _invoice(engines, entities={k: v for k, v in WRONG.items() if k != "line_items"},
                            agents=[{**TRUTH, "invoice_number": "PO-551"}] * 2)

    response = engines.patch(f"/work-items/{work_item_id}/fields", {"corrections": {"invoice_number": "INV-FIX-1"}},
                             as_user=engines.tenant.contributor)

    assert response.status_code == 200, response.text
    assert response.json()["extraction_memory_fields"] == ["invoice_number"]
    engines.refresh()
    exemplar = engines.db.execute(
        select(ExtractionExemplar).where(ExtractionExemplar.work_item_id == work_item_id,
                                         ExtractionExemplar.field_path == "invoice_number")
    ).scalar_one()
    assert exemplar.label_source == "CORRECTED"
    summary = engines.get("/extraction-memory/summary").json()
    assert summary["exemplar_documents"] == 1 and summary["corrected_exemplars"] >= 1, summary

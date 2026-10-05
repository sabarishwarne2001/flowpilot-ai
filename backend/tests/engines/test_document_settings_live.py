"""Document processing settings that decide whether the engines get any data.

Two defects, one file:

* Opening Settings -> Document processing before the first upload stored the
  API's defaults, which had entity extraction and summaries OFF, while the
  worker's own defaults have them ON. A workspace whose admin looked at the
  page first therefore extracted no fields at all, and every engine that reads
  fields (matching, ERP, entity graph, cases, radar, obligations) silently got
  nothing.
* Multi-agent extraction verification (`verification_enabled`) had no API and
  no control, so it could never be turned on: no extraction review item was
  ever created, and extraction memory (which learns only from those reviews)
  could never learn.
"""

from __future__ import annotations

from sqlalchemy import select

from app.models.verification import DocumentVerification
from tests.engines.conftest import Engines

INVOICE = ["ACME SUPPLIES LTD", "TAX INVOICE", "Invoice Number: INV-SET-1", "Total: 99.00 USD"]
ENTITIES = {"vendor_name": "Acme Supplies Ltd", "invoice_number": "INV-SET-1", "total_amount": "99.00"}


def test_opening_the_settings_page_first_keeps_extraction_on(engines: Engines) -> None:
    shown = engines.get("/document-settings/")
    assert shown.status_code == 200, shown.text
    assert shown.json()["automatic_entity_extraction"] is True
    assert shown.json()["automatic_summarization"] is True

    work_item_id = engines.process("inv.pdf", [INVOICE], marker="INV-SET-1", classification="Invoice", entities=ENTITIES)
    item = engines.item(work_item_id)
    assert item.extracted_entities.get("invoice_number") == "INV-SET-1", item.extracted_entities
    assert item.summary


def test_verification_can_be_turned_on_and_runs(engines: Engines) -> None:
    current = engines.get("/document-settings/").json()
    assert current["verification_enabled"] is False
    response = engines.put("/document-settings/", {"verification_enabled": True, "verification_agents": 3})
    assert response.status_code == 200, response.text
    assert response.json()["verification_enabled"] is True
    assert response.json()["verification_agents"] == 3

    # A later save of another setting keeps it on (the page sends what it shows).
    again = engines.put("/document-settings/", {"max_upload_size": 40})
    assert again.status_code == 200, again.text
    assert again.json()["verification_enabled"] is True
    assert again.json()["max_upload_size"] == 40

    refused = engines.put("/document-settings/", {"verification_enabled": False}, as_user=engines.tenant.contributor)
    assert refused.status_code == 403

    work_item_id = engines.process("inv.pdf", [INVOICE], marker="INV-SET-1", classification="Invoice", entities=ENTITIES)
    engines.refresh()
    verification = engines.db.execute(
        select(DocumentVerification).where(DocumentVerification.work_item_id == work_item_id)).scalar_one()
    assert verification.agent_count == 3
    # Enterprise carries calibrated autonomy. With no fitted model yet (a new tenant) nothing is approved
    # automatically: the document is held and the reviewer confirms every field (the labels the model
    # will be fitted on). Three identical agents still score full confidence.
    assert verification.status.value == "DISAGREED", verification.status
    assert verification.details["calibration"]["review_all_fields"] is True
    assert float(verification.confidence) == 1.0


def test_without_calibrated_autonomy_agreeing_agents_approve_automatically(engines: Engines) -> None:
    engines.plan("business")
    assert engines.put("/document-settings/", {"verification_enabled": True}).status_code == 200
    work_item_id = engines.process("inv.pdf", [INVOICE], marker="INV-SET-1", classification="Invoice", entities=ENTITIES)
    engines.refresh()
    verification = engines.db.execute(
        select(DocumentVerification).where(DocumentVerification.work_item_id == work_item_id)).scalar_one()
    assert verification.status.value == "AGREED", (verification.status, verification.details)
    assert verification.auto_approved is True

    # Agents that disagree on one field send the document to review, with only that field to decide.
    engines.llm.record("INV-SET-2", "Invoice", {**ENTITIES, "invoice_number": "INV-SET-2"}, agents=[
        {**ENTITIES, "invoice_number": "INV-SET-2"}, {**ENTITIES, "invoice_number": "INV-SET-Z"}])
    second = engines.process("inv2.pdf", [[*INVOICE[:2], "Invoice Number: INV-SET-2", INVOICE[3]]])
    engines.refresh()
    disagreed = engines.db.execute(
        select(DocumentVerification).where(DocumentVerification.work_item_id == second)).scalar_one()
    assert disagreed.status.value == "DISAGREED"
    assert disagreed.details["disagreed_fields"] == ["invoice_number"]

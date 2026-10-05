"""The live pipeline the engine tests stand on: upload -> extract -> enrich -> fan-out."""

from __future__ import annotations

from sqlalchemy import select

from app.models.document_role import DocumentRole
from app.models.job import Job
from tests.engines.conftest import Engines
from tests.engines.isolation import assert_workspace_isolated

INVOICE = [
    "ACME SUPPLIES LTD",
    "TAX INVOICE",
    "Invoice Number: INV-PIPE-1001",
    "Invoice Date: 2026-09-01",
    "PO Number: PO-PIPE-5001",
    "Bill To: Contoso Retail",
    "Item  Qty  Unit Price  Amount",
    "Steel bolts M8  100  2.50  250.00",
    "Total: 250.00 USD",
]


def test_an_uploaded_invoice_reaches_every_engine(engines: Engines) -> None:
    work_item_id = engines.process(
        "invoice.pdf", [INVOICE], marker="INV-PIPE-1001", classification="Invoice",
        entities={"vendor_name": "Acme Supplies Ltd", "invoice_number": "INV-PIPE-1001", "total_amount": "250.00",
                  "currency": "USD", "date": "2026-09-01", "po_number": "PO-PIPE-5001"},
    )
    item = engines.item(work_item_id)
    assert item.status == "COMPLETED", (item.status, item.extraction_metadata)
    assert "INV-PIPE-1001" in (item.extracted_text or "")
    assert item.extracted_entities["vendor_name"] == "Acme Supplies Ltd"
    role = engines.db.execute(select(DocumentRole).where(DocumentRole.work_item_id == work_item_id)).scalar_one()
    assert role.role == "INVOICE"
    job_types = set(engines.db.execute(select(Job.job_type)).scalars())
    assert {"document.extract", "document.enrich", "anomaly.scan_document", "procurement.score",
            "entities.resolve_document", "cases.assemble_document"} <= job_types, job_types

    # Usage is metered per workspace: the real rollup turns this document's metered events into usage
    # lines that workspace A reports and a second workspace of the same organization never does.
    from app.services.rollup_service import run_rollup

    run_rollup(engines.db)
    engines.refresh()
    assert_workspace_isolated(engines, collections=("work-items", "usage"),
                              aggregates=("usage/summary", "usage/series"))

"""Scanned packets, live: one PDF holding several documents is detected, split and re-processed.

A five-page bundle: a two-page invoice, a purchase order, a goods receipt and a
second invoice. Uploading it runs boundary detection on its own; the detected
plan is checked against the true boundaries (pages 3, 4 and 5 start new
documents); approving it splits the PDF into four real child PDFs, each a new
work item with the right pages, its own text, a link to its parent, and its
own pass through enrichment (so each gets its own procurement role).
"""

from __future__ import annotations

import io
import uuid

import pypdf
from sqlalchemy import select

from app.core.storage import get_storage_driver
from app.models.document_role import DocumentRole
from app.models.work_item import WorkItem
from tests.engines.conftest import Engines, drain, make_pdf
from tests.engines.isolation import assert_workspace_isolated

PAGES = [
    ["ACME SUPPLIES LTD", "TAX INVOICE", "Invoice Number: INV-PK-1", "Page 1 of 2", "Steel bolts M8  100  2.50"],
    ["ACME SUPPLIES LTD", "Invoice Number: INV-PK-1", "Page 2 of 2", "Total: 250.00 USD", "Amount due: 250.00"],
    ["CONTOSO RETAIL", "PURCHASE ORDER", "PO Number: PO-PK-1", "Page 1 of 1", "Ship To: Warehouse 4"],
    ["CONTOSO RETAIL", "GOODS RECEIPT NOTE", "GRN Number: GRN-PK-1", "Page 1 of 1", "Quantity received 100"],
    ["GLOBEX CORPORATION", "TAX INVOICE", "Invoice Number: INV-PK-2", "Page 1 of 1", "Total: 90.00 USD"],
]


def test_a_bundle_is_detected_split_and_each_part_processed(engines: Engines) -> None:
    for marker, kind in (("INV-PK-1", "Invoice"), ("PO-PK-1", "Purchase Order"), ("GRN-PK-1", "Goods Receipt"),
                         ("INV-PK-2", "Invoice")):
        engines.llm.record(marker, kind, {"document_number": marker})
    parent_id = engines.upload("bundle.pdf", make_pdf(PAGES))
    drain()

    splits = engines.get("/packet-splits").json()["items"]
    [split] = [s for s in splits if str(s["work_item_id"]) == str(parent_id)]
    assert split["status"] == "PROPOSED", split
    detail = engines.get(f"/packet-splits/{split['id']}").json()
    detected = [segment["page_start"] for segment in detail["segments"]][1:]
    assert detected == [3, 4, 5], [(s["page_start"], s["page_end"], s["document_type"]) for s in detail["segments"]]
    assert [s["document_type"] for s in detail["segments"]][:3] == ["invoice", "purchase_order", "goods_receipt"]

    assert engines.post(f"/packet-splits/{split['id']}/approve", as_user=engines.tenant.viewer).status_code == 403
    approved = engines.post(f"/packet-splits/{split['id']}/approve", as_user=engines.tenant.contributor)
    assert approved.status_code == 200, approved.text
    drain()

    engines.refresh()
    children = list(engines.db.execute(select(WorkItem).where(WorkItem.parent_work_item_id == parent_id)
                                       .order_by(WorkItem.parent_page_start)).scalars())
    assert [(c.parent_page_start, c.parent_page_end) for c in children] == [(1, 2), (3, 3), (4, 4), (5, 5)]
    storage = get_storage_driver()
    for child, pages in zip(children, (2, 1, 1, 1)):
        assert child.page_count == pages
        assert len(pypdf.PdfReader(io.BytesIO(storage.get(child.stored_filename))).pages) == pages
        assert child.status == "COMPLETED", (child.original_filename, child.status)
    assert "INV-PK-1" in children[0].extracted_text and "PO-PK-1" not in children[0].extracted_text

    roles = {r.work_item_id: r.role for r in engines.db.execute(
        select(DocumentRole).where(DocumentRole.work_item_id.in_([c.id for c in children]))).scalars()}
    assert [roles.get(c.id) for c in children] == ["INVOICE", "PURCHASE_ORDER", "GOODS_RECEIPT", "INVOICE"], roles

    lineage = engines.get(f"/work-items/{children[1].id}/lineage")
    assert lineage.status_code == 200, lineage.text
    assert str(parent_id) in lineage.text

    thumb = engines.get(f"/packet-splits/{split['id']}/pages/1/thumbnail")
    assert thumb.status_code == 200 and thumb.headers["content-type"].startswith("image/")
    assert_workspace_isolated(engines, collections=("packet-splits",))

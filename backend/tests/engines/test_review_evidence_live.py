"""Review evidence, live: the page image with each disputed reading highlighted.

An invoice prints its invoice number on one line and its PO reference on the
next. One verification agent reads the invoice number as the PO reference,
so the document reaches the review hub. The reviewer's evidence then shows:

* the page as an image (PNG, the page's proportions);
* where the document's current reading (the PO reference) and the other
  agent's reading (the invoice number) are printed - two boxes on page 1,
  on different lines, each inside the page and narrowed to the value;
* "1,250.00" is found for the extracted "1250.00" (separators ignored), and
  a value the page never prints is reported as not found, not guessed;
* a VIEWER may look; another workspace may not.
"""

from __future__ import annotations

import io

from PIL import Image

from tests.engines.conftest import Engines

LINES = ["ACME SUPPLIES LTD", "TAX INVOICE", "Invoice No: INV-EV-1", "PO Reference: PO-EV-1",
         "Amount due: 1,250.00 INR"]


def test_the_reviewer_sees_where_each_reading_is_printed(engines: Engines) -> None:
    engines.plan("business")
    assert engines.put("/document-settings/", {"verification_enabled": True, "verification_agents": 2}).status_code == 200
    wrong = {"vendor_name": "Acme Supplies Ltd", "invoice_number": "PO-EV-1", "total_amount": "1250.00"}
    right = {**wrong, "invoice_number": "INV-EV-1"}
    work_item_id = engines.process("ev.pdf", [LINES], marker="INV-EV-1", classification="Invoice",
                                   entities=wrong, agents=[wrong, right])
    queue = engines.get("/review", params={"kind": "EXTRACTION"}).json()["items"]
    assert [str(i["work_item_id"]) for i in queue] == [str(work_item_id)], queue

    evidence = engines.get(f"/work-items/{work_item_id}/evidence",
                           params={"candidate": ["invoice_number:INV-EV-1", "invoice_number:ZZZ-NOT-PRINTED"]},
                           as_user=engines.tenant.viewer)
    assert evidence.status_code == 200, evidence.text
    body = evidence.json()
    assert body["renderable"] is True and [p["page_number"] for p in body["pages"]] == [1]
    by_value = {(loc["value"], loc["source"]): loc for loc in body["locations"]}
    current, other = by_value[("PO-EV-1", "extracted")], by_value[("INV-EV-1", "candidate")]
    for box in (current, other):
        assert box["page"] == 1 and box["field"] == "invoice_number"
        assert 0 <= box["x0"] < box["x1"] <= 1 and 0 <= box["y0"] < box["y1"] <= 1, box
    assert other["y1"] <= current["y0"], "the invoice number is printed on the line above the PO reference"
    assert current["x0"] > 0.1, "the box is narrowed to the value, not the whole 'PO Reference:' line"
    assert ("1250.00", "extracted") in by_value, body["locations"]
    assert {"field": "invoice_number", "value": "ZZZ-NOT-PRINTED", "source": "candidate"} in body["not_found"]

    page = engines.get(f"/work-items/{work_item_id}/pages/1.png", params={"dpi": 72}, as_user=engines.tenant.viewer)
    assert page.status_code == 200 and page.headers["content-type"] == "image/png"
    with Image.open(io.BytesIO(page.content)) as image:
        assert abs(image.width / image.height - 595 / 842) < 0.01, image.size  # A4
    assert engines.get(f"/work-items/{work_item_id}/pages/2.png").status_code == 404

    foreign = engines.client.get(f"/api/v1/workspaces/{engines.tenant.foreign_workspace.id}/work-items/"
                                 f"{work_item_id}/evidence", headers=engines.tenant.other_org_member.headers)
    assert foreign.status_code == 404, foreign.text

"""F-162: the Documents bulk export (CSV) wrote extracted values into cells as they were.

A value read from a hostile document, such as `=HYPERLINK("http://x","Click")` in a vendor name or
`@SUM(1+1)` in a reference, ran as a spreadsheet formula when the export was opened in Excel or
Sheets. Table, obligation and audit exports already neutralised these; the bulk export did not.
The JSON export keeps values exactly as extracted.
"""

from __future__ import annotations

import csv
import io
import json


def _export(client, tenant, ids, fmt):
    response = client.post(
        f"/api/v1/workspaces/{tenant.workspace.id}/work-items/bulk",
        json={"action": "export", "ids": [str(i) for i in ids], "export_format": fmt, "idempotency_key": f"export-{fmt}-{ids[0]}"},
        headers=tenant.owner.headers,
    )
    assert response.status_code == 200, response.text
    return response.json()["export_body"]


def test_formula_values_are_neutralised_in_the_csv(client, db_session, tenant, work_item_factory):
    item = work_item_factory(
        original_filename="=cmd.pdf",
        extracted_entities={
            "vendor_name": '=HYPERLINK("http://evil.example","Click")',
            "reference": "@SUM(1+1)",
            "note": "+1 555 0100",
            "discount": "-10",
            "total_amount": 120.5,
            "plain": "Acme Ltd",
        },
    )
    db_session.commit()

    rows = list(csv.DictReader(io.StringIO(_export(client, tenant, [item.id], "csv"))))

    assert rows[0]["filename"] == "'=cmd.pdf"
    assert rows[0]["field.vendor_name"] == "'=HYPERLINK(\"http://evil.example\",\"Click\")"
    assert rows[0]["field.reference"] == "'@SUM(1+1)"
    assert rows[0]["field.note"] == "'+1 555 0100"
    assert rows[0]["field.discount"] == "'-10"
    assert rows[0]["field.total_amount"] == "120.5"
    assert rows[0]["field.plain"] == "Acme Ltd"


def test_the_json_export_keeps_values_exactly(client, db_session, tenant, work_item_factory):
    item = work_item_factory(extracted_entities={"vendor_name": "=1+1"})
    db_session.commit()

    rows = json.loads(_export(client, tenant, [item.id], "json"))

    assert rows[0]["field.vendor_name"] == "=1+1"

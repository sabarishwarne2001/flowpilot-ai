"""Every action on one matching case and one extracted table works, live (campaign session 2, ledger).

    pytest tests/engines/test_item_actions_procurement_tables_live.py -q

These routes had only their refusals proven. On a real order, receipt and invoice read through
the pipeline: a person corrects a document's role, re-matches the case to a chosen order and
receipt, disputes it with a reason; on the invoice's table, a column's role is set, the table is
re-extracted and exported as a spreadsheet and as JSON.
"""

from __future__ import annotations

from tests.engines.conftest import Engines, drain
from tests.engines.test_three_way_matching_live import _set, policy  # noqa: F401  (fixture)


def test_every_case_action_works(engines: Engines, policy) -> None:  # noqa: F811
    case = _set(engines, "IA-CASE", "Acme Supplies Ltd", received=100, invoice_price="2.50")
    invoice_id = case["invoice_work_item_id"]

    role = engines.put(f"/procurement/roles/{case['receipt_work_item_id']}", {"role": "GOODS_RECEIPT"})
    assert role.status_code == 200, role.text

    rematch = engines.post(f"/procurement/cases/{case['id']}/rematch",
                           {"po_work_item_id": case["po_work_item_id"],
                            "receipt_work_item_id": case["receipt_work_item_id"]})
    assert rematch.status_code == 202, rematch.text
    drain()
    cases = [c for c in engines.get("/procurement/cases").json() if c["invoice_work_item_id"] == invoice_id]
    assert len(cases) == 1, cases  # re-matching never opens a second case for the invoice
    current = cases[0]

    too_short = engines.post(f"/procurement/cases/{current['id']}/dispute", {"reason": "wrong"})
    assert too_short.status_code == 422, too_short.text
    disputed = engines.post(f"/procurement/cases/{current['id']}/dispute",
                            {"reason": "Supplier billed boots we never ordered."})
    assert disputed.status_code == 200, disputed.text
    assert disputed.json()["status"] == "DISPUTED"


def test_every_table_action_works(engines: Engines) -> None:
    import uuid

    from tests.fixtures.corpus import build

    engines.plan("business")
    filename, data, mime = build("invoice_match")  # a table drawn in real columns
    uploaded = engines.client.post(engines.url("/work-items"), files={"file": (filename, data, mime)},
                                   headers=engines.tenant.owner.headers)
    assert uploaded.status_code == 201, uploaded.text
    work_item_id = uuid.UUID(uploaded.json()["id"])
    drain()

    again = engines.post(f"/work-items/{work_item_id}/tables/extract")
    assert again.status_code in (200, 202), again.text
    drain()
    tables = engines.get(f"/work-items/{work_item_id}/tables").json()["tables"]
    assert tables, "no table found on the invoice"
    table = tables[0]

    role = engines.put(f"/tables/{table['id']}/columns/1/role", {"role": "QUANTITY"})
    assert role.status_code == 200, role.text

    xlsx = engines.get(f"/work-items/{work_item_id}/tables/export", params={"format": "xlsx"})
    assert xlsx.status_code == 200 and xlsx.content[:2] == b"PK", xlsx.status_code  # a zip: a real workbook
    as_json = engines.get(f"/work-items/{work_item_id}/tables/export", params={"format": "json"})
    assert as_json.status_code == 200, as_json.text
    assert "Industrial safety gloves" in as_json.text

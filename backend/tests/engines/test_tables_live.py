"""Table intelligence, live: a ruled two-page table is read, checked, corrected and exported.

The PDF is a real ruled table (reportlab platypus, header repeated on page 2)
of 40 line items `Item | Qty | Rate | Amount` and a TOTAL row. Rates
vary (so no column is a running balance of another). Row 7's amount is
printed 5.00 too high (qty 7 x rate 11.00 = 77.00, printed 82.00), and the TOTAL is
the true sum, so:

* the engine finds ONE table spanning pages 1-2 (not two), discovers
  amount = qty x rate and total = sum(amounts) from the data, and flags
  exactly the bad cell (the total "explained" by it, not flagged twice);
* the table is FLAGGED and a trigger event is emitted;
* correcting the cell re-validates the table (no failed checks), a reviewer
  ACCEPTs it (REVIEWED), and the CSV / XLSX exports carry the corrected value;
* a VIEWER can read and export but cannot correct.
"""

from __future__ import annotations

import csv
import io
import zipfile
from decimal import Decimal

import pytest

from tests.engines.conftest import Engines, drain
from tests.engines.isolation import assert_workspace_isolated

ROWS = 40
BAD_ROW = 7


def _rate(i: int) -> Decimal:
    return Decimal("8.50") + Decimal(i % 5) * Decimal("1.25")


TRUE_TOTAL = sum(i * _rate(i) for i in range(1, ROWS + 1))
GOOD = BAD_ROW * _rate(BAD_ROW)


def _table_pdf() -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Table, TableStyle
    from reportlab.lib.styles import getSampleStyleSheet

    data = [["Item", "Qty", "Rate", "Amount"]]
    total = Decimal(0)
    for i in range(1, ROWS + 1):
        qty, rate = i, _rate(i)
        amount = qty * rate
        total += amount
        printed = amount + 5 if i == BAD_ROW else amount
        data.append([f"Part {i:03d}", str(qty), f"{rate:.2f}", f"{printed:.2f}"])
    data.append(["TOTAL", "", "", f"{total:.2f}"])
    table = Table(data, repeatRows=1, colWidths=[200, 60, 80, 100])
    table.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.5, colors.black),
                               ("ALIGN", (1, 1), (-1, -1), "RIGHT")]))
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4)
    doc.build([Paragraph("ACME SUPPLIES LTD - TABLE-LIVE-1 Statement of parts", getSampleStyleSheet()["Title"]), table])
    return buffer.getvalue()


@pytest.fixture()
def table(engines: Engines) -> dict:
    engines.llm.record("TABLE-LIVE-1", "Invoice", {"vendor_name": "Acme Supplies Ltd"})
    work_item_id = engines.upload("parts.pdf", _table_pdf())
    drain()
    listed = engines.get(f"/work-items/{work_item_id}/tables").json()
    assert listed["tables"], listed
    assert len(listed["tables"]) == 1, [(t["page_start"], t["page_end"], t["n_rows"]) for t in listed["tables"]]
    return engines.get(f"/tables/{listed['tables'][0]['id']}").json()


def _cell(detail: dict, row: int, col: int) -> dict:
    return next(c for c in detail["cells"] if c["row"] == row and c["col"] == col)


def _row_of(detail: dict, label: str) -> int:
    return next(c["row"] for c in detail["cells"] if c["col"] == 0 and c["text"].strip() == label)


def test_a_two_page_table_is_one_table_with_the_bad_cell_flagged(engines: Engines, table: dict) -> None:
    summary = table["table"]
    assert (summary["page_start"], summary["page_end"]) == (1, 2), summary
    assert summary["n_cols"] == 4
    body_rows = [r for r in table["rows"] if r["kind"] == "BODY"]
    assert len(body_rows) == ROWS, [r["kind"] for r in table["rows"]]
    assert summary["status"] == "FLAGGED", summary

    failed = [v for v in table["validations"] if v["outcome"] == "FAIL" and v["scope"] == "CELL"]
    bad = _row_of(table, f"Part {BAD_ROW:03d}")
    assert [(v["kind"], v["row_index"], v["col_index"]) for v in failed] == [("ROW_PRODUCT", bad, 3)], failed
    assert Decimal(failed[0]["expected"]) == GOOD
    kinds = {v["kind"] for v in table["validations"]}
    assert {"ROW_PRODUCT", "COLUMN_SUM"} <= kinds, kinds

    from sqlalchemy import select

    from app.models.outbox_event import OutboxEvent

    engines.refresh()
    assert "trigger.table.flagged" in set(engines.db.execute(select(OutboxEvent.event_type)).scalars())


def test_correct_review_and_export(engines: Engines, table: dict) -> None:
    table_id = table["table"]["id"]
    bad = _row_of(table, f"Part {BAD_ROW:03d}")
    edit = {"cells": [{"row": bad, "col": 3, "text": f"{GOOD:.2f}"}]}
    assert engines.patch(f"/tables/{table_id}/cells", edit, as_user=engines.tenant.viewer).status_code == 403

    corrected = engines.patch(f"/tables/{table_id}/cells", edit, as_user=engines.tenant.contributor)
    assert corrected.status_code == 200, corrected.text
    after = corrected.json()
    assert after["table"]["failed_checks"] == 0, [v for v in after["validations"] if v["outcome"] == "FAIL"]
    assert _cell(after, bad, 3)["text"] == f"{GOOD:.2f}"

    reviewed = engines.post(f"/tables/{table_id}/review", {"verdict": "ACCEPT"}, as_user=engines.tenant.contributor)
    assert reviewed.status_code == 200, reviewed.text
    assert reviewed.json()["table"]["status"] == "REVIEWED"

    as_csv = engines.get(f"/tables/{table_id}/export", params={"format": "csv"}, as_user=engines.tenant.viewer)
    assert as_csv.status_code == 200, as_csv.text
    rows = list(csv.reader(io.StringIO(as_csv.content.decode("utf-8-sig"))))
    assert ["Part 007", "7", f"{_rate(BAD_ROW):.2f}", f"{GOOD:.2f}"] in [r[:4] for r in rows], rows[:10]
    assert any(r and r[0] == "TOTAL" and r[3] == f"{TRUE_TOTAL:.2f}" for r in rows), rows[-3:]

    as_xlsx = engines.get(f"/tables/{table_id}/export", params={"format": "xlsx"})
    assert as_xlsx.status_code == 200, as_xlsx.text
    with zipfile.ZipFile(io.BytesIO(as_xlsx.content)) as book:
        names = book.namelist()
        assert "xl/workbook.xml" in names, names
        sheet = book.read(next(n for n in names if n.startswith("xl/worksheets/"))).decode()
    assert "Part 007" in sheet and f"<v>{GOOD:.2f}</v>" in sheet, sheet[:400]
    assert_workspace_isolated(engines, collections=("tables",))

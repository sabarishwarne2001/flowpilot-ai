"""A digital PDF's table rows reach the model as rows (campaign session 2, F-265).

    pytest tests/services/test_pdf_text_layer_lines.py -q

`pdf_text_layer.extract_page` made one block, and one line of page text, per text run. A
table drawn cell by cell (most generated invoices, purchase orders and statements) came out
as one cell per line ("1", "Industrial safety gloves", "100", "5.00", "500.00"), so the row
structure was gone before any model read it: line items could not be read, three-way
matching compared nothing, and "Total:" and its amount sat on different lines. The module
was designed to group runs into LINES; it never did. Now runs on one visual line form one
block (its box the union of theirs), so the page text keeps rows and the citation boxes and
the span mapping (page text is the join of block texts) hold.
"""

from __future__ import annotations

import io

import pypdfium2 as pdfium
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

from app.services.document_models import DocumentPage
from app.services.ocr.pdf_text_layer import extract_page, verify_span_mapping


def _table_pdf() -> bytes:
    buffer = io.BytesIO()
    page = canvas.Canvas(buffer, pagesize=A4)
    page.setFont("Helvetica", 11)
    y = A4[1] - 60
    page.drawString(50, y, "INVOICE")
    y -= 20
    for x, label in ((50, "Total:"), (160, "1250.00 USD")):  # a label and its value, drawn apart
        page.drawString(x, y, label)
    y -= 30
    columns = (50, 80, 260, 320, 400)
    for row in (("#", "Description", "Qty", "Unit Price", "Amount"),
                ("1", "Industrial safety gloves", "100", "5.00", "500.00"),
                ("2", "Steel toe boots", "10", "60.00", "600.00")):
        for x, cell in zip(columns, row):
            page.drawString(x, y, cell)
        y -= 18
    page.showPage()
    page.save()
    return buffer.getvalue()


def _lines() -> tuple[list[str], object]:
    document = pdfium.PdfDocument(_table_pdf())
    try:
        extracted = extract_page(document[0])
    finally:
        document.close()
    assert extracted is not None
    return [" ".join(line.split()) for line in extracted.text.splitlines()], extracted


def test_a_table_row_is_one_line() -> None:
    lines, _ = _lines()
    assert "1 Industrial safety gloves 100 5.00 500.00" in lines, lines
    assert "# Description Qty Unit Price Amount" in lines, lines


def test_a_label_and_its_value_on_one_line_stay_together() -> None:
    lines, _ = _lines()
    assert "Total: 1250.00 USD" in lines, lines


def test_citation_boxes_still_map_onto_the_text() -> None:
    _, extracted = _lines()
    assert extracted.text == "\n".join(block.text for block in extracted.blocks)
    assert verify_span_mapping(extracted.text, extracted.blocks)
    page = DocumentPage(
        page_number=1,
        text=extracted.text,
        blocks=[{"text": b.text, "confidence": b.confidence, "box": b.box.__dict__ if hasattr(b.box, "__dict__") else b.box}
                for b in extracted.blocks],
    )
    spans, status = page.block_spans()
    assert status == "FROM_BLOCKS" or spans, status
    row = next(block for block in extracted.blocks if "Industrial safety gloves" in block.text)
    assert row.box.x0 < 50 * 200 / 72 + 5 and row.box.x1 > 400 * 200 / 72, "a row's box spans its cells"

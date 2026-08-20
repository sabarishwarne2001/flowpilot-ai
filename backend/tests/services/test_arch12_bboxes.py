"""Gate 12.5 (F7) — digital PDFs must emit bounding boxes.

The assertion that matters is not "blocks exist". It is that the boxes
**survive chunking**, which requires `DocumentPage.block_spans()` to map every
block onto a character span in the page text. That mapping is the invariant
`pdf_text_layer` is built around, and it is exactly what a well-meaning
formatting change would break — silently, producing NULL bboxes on every new
document while every unit test still passed.

So the gate runs the real path end to end: build a PDF, extract it, feed the
result through `DocumentPage`, chunk it, and assert the chunks carry boxes
sourced from blocks.
"""

from __future__ import annotations

import pathlib
from typing import Any

import pytest

from app.services.document_models import (
    BBOX_FROM_BLOCKS,
    BBOX_NO_BLOCKS,
    DocumentPage,
)
from app.services.ocr.pdf_text_layer import (
    POINTS_PER_INCH,
    extract_page,
    verify_span_mapping,
)

pdfium = pytest.importorskip("pypdfium2")
pytestmark = pytest.mark.no_db

RASTER_DPI = 200
SCALE = RASTER_DPI / POINTS_PER_INCH


@pytest.fixture(scope="module")
def digital_pdf(tmp_path_factory) -> pathlib.Path:
    """A real text-layer PDF. Built with reportlab if present, else raw."""
    target = tmp_path_factory.mktemp("f7") / "digital.pdf"

    try:
        from reportlab.lib.pagesizes import A4  # type: ignore[import-not-found,import-untyped]
        from reportlab.pdfgen import canvas  # type: ignore[import-not-found,import-untyped]

        surface = canvas.Canvas(str(target), pagesize=A4)
        surface.setFont("Helvetica", 12)
        for index, line in enumerate(
            [
                "FLOWPILOT SERVICES INVOICE",
                "Invoice number: FP-2026-00417",
                "Vendor: Northwind Analytics Ltd",
                "Total amount due: EUR 4,200.00",
                "Payment terms: net thirty days from issue",
            ]
        ):
            surface.drawString(72, 760 - index * 24, line)
        surface.showPage()
        surface.save()
    except ImportError:  # pragma: no cover - reportlab is optional
        target.write_bytes(_minimal_pdf())

    return target


def _minimal_pdf() -> bytes:
    """A hand-rolled single-page PDF with a real text layer."""
    content = (
        b"BT /F1 12 Tf 72 760 Td (FLOWPILOT SERVICES INVOICE) Tj "
        b"0 -24 Td (Invoice number: FP-2026-00417) Tj "
        b"0 -24 Td (Total amount due: EUR 4,200.00) Tj ET"
    )
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
        b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
        b"<< /Length " + str(len(content)).encode() + b" >>\nstream\n" + content + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]

    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for index, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{index} 0 obj\n".encode() + body + b"\nendobj\n"

    xref_at = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode()
    out += b"0000000000 65535 f \n"
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
        f"startxref\n{xref_at}\n%%EOF\n"
    ).encode()
    return bytes(out)


# ---------------------------------------------------------------------------


def test_digital_page_yields_boxed_blocks(digital_pdf):
    document = pdfium.PdfDocument(str(digital_pdf))
    try:
        page = extract_page(document[0], raster_dpi=RASTER_DPI)
    finally:
        document.close()

    assert page is not None, "a text-layer PDF must not fall through to OCR"
    assert page.blocks, "F7: digital pages used to arrive with blocks=[]"
    assert page.boxed_block_count == len(page.blocks)
    assert "FLOWPILOT" in page.text


def test_page_text_is_the_join_of_block_texts(digital_pdf):
    """The invariant `DocumentPage.block_spans()` depends on."""
    document = pdfium.PdfDocument(str(digital_pdf))
    try:
        page = extract_page(document[0], raster_dpi=RASTER_DPI)
    finally:
        document.close()

    assert page.text == "\n".join(block.text for block in page.blocks)
    assert verify_span_mapping(page.text, page.blocks)


def test_block_spans_resolve_rather_than_reporting_no_blocks(digital_pdf):
    document = pdfium.PdfDocument(str(digital_pdf))
    try:
        extracted = extract_page(document[0], raster_dpi=RASTER_DPI)
    finally:
        document.close()

    page = DocumentPage(
        page_number=1,
        text=extracted.text,
        blocks=[block.as_dict() for block in extracted.blocks],
        width=extracted.width,
        height=extracted.height,
        ocr_applied=False,
    )

    spans, status = page.block_spans()
    assert status == BBOX_FROM_BLOCKS
    assert status != BBOX_NO_BLOCKS, "this is the exact F7 regression"
    assert len(spans) == len(extracted.blocks)

    for span in spans:
        assert page.text[span.start : span.end] == span.text


def test_coordinates_are_pixels_top_left_not_points_bottom_left(digital_pdf):
    """A points/pixels mix-up renders every digital box at a third scale."""
    document = pdfium.PdfDocument(str(digital_pdf))
    try:
        pdf_page = document[0]
        height_points = float(pdf_page.get_height())
        extracted = extract_page(pdf_page, raster_dpi=RASTER_DPI)
    finally:
        document.close()

    assert extracted.height == pytest.approx(height_points * SCALE, rel=0.01)

    # Text drawn near the top of the page must have a SMALL y0 in top-left
    # pixel space. In PDF points it would have a LARGE y — that inversion is
    # the bug this asserts against.
    first = extracted.blocks[0].box
    assert first.y0 < extracted.height / 2
    assert first.x1 > first.x0 and first.y1 > first.y0
    assert first.x1 <= extracted.width * 1.02


def test_chunks_carry_boxes_end_to_end(digital_pdf):
    from app.services.chunking_service import split_pages

    document = pdfium.PdfDocument(str(digital_pdf))
    try:
        extracted = extract_page(document[0], raster_dpi=RASTER_DPI)
    finally:
        document.close()

    page = DocumentPage(
        page_number=1,
        text=extracted.text,
        blocks=[block.as_dict() for block in extracted.blocks],
        width=extracted.width,
        height=extracted.height,
        ocr_applied=False,
    )

    class _WordTokenizer:
        def encode(self, text: str) -> list[int]:
            return list(range(len(text.split())))

        def __call__(self, text: Any, **kwargs: Any) -> Any:
            if isinstance(text, list):
                return {"input_ids": [list(range(len(t.split()))) for t in text]}
            return {"input_ids": list(range(len(str(text).split())))}

    candidates = split_pages(
        [page], tokenizer=_WordTokenizer(), chunk_size_tokens=64, chunk_overlap_pct=0
    )

    assert candidates
    boxed = [candidate for candidate in candidates if candidate.bbox]
    assert boxed, "F7 is not closed unless the boxes reach the chunk"

    for candidate in boxed:
        assert candidate.bbox_source == BBOX_FROM_BLOCKS
        assert candidate.bbox["space"] == "pixels"
        assert candidate.bbox["page"] == 1
        # ARCH-11 Step 3's offset invariant, restated here because it is what
        # makes boxes re-derivable without a re-embed.
        assert (
            candidate.content
            == page.text[candidate.page_start_char : candidate.page_end_char]
        )


def test_extract_page_returns_none_for_a_scanned_page(tmp_path):
    """No text layer must fall through to rasterisation, not fabricate boxes."""
    blank = tmp_path / "blank.pdf"
    raw = (
        _minimal_pdf()
        .replace(b"FLOWPILOT SERVICES INVOICE", b" ")
        .replace(b"Invoice number: FP-2026-00417", b" ")
        .replace(b"Total amount due: EUR 4,200.00", b" ")
    )
    blank.write_bytes(raw)

    document = pdfium.PdfDocument(str(blank))
    try:
        page = extract_page(document[0], raster_dpi=RASTER_DPI)
    finally:
        document.close()

    assert page is None or page.character_count < 24
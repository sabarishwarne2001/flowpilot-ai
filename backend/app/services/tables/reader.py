"""ARCH44-S1:reader — a stored document as engine input. No database; reads bytes.

For each page, the cheapest faithful source:

  * a page OCR'd by the ARCH-10 pipeline (`ocr_applied`) is read from the
    blocks already stored in `extraction_metadata["pages"]` -- nothing is
    re-OCR'd and nothing is billed twice. When the original is a PDF, the page
    is also rendered once at 100 DPI so ruling lines can be found in the
    pixels (numpy; no OpenCV, no Ghostscript).
  * a page with a text layer is read from the PDF itself, word by word, with
    ruling lines from its vector paths: the stored blocks for such pages are
    whole lines, which would glue a table row into one token.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from app.services.tables import vocabulary as v
from app.services.tables.geometry import PageInput, page_from_ocr, page_from_pdf, rules_from_bitmap

logger = logging.getLogger("app.services.tables.reader")

RULE_RENDER_DPI = 100


def _ocr_entries(metadata: Optional[dict]) -> dict[int, dict]:
    pages = (metadata or {}).get("pages") or []
    out: dict[int, dict] = {}
    for entry in pages:
        if isinstance(entry, dict) and entry.get("page_number"):
            out[int(entry["page_number"])] = entry
    return out


def pages_for(*, pdf_bytes: Optional[bytes], metadata: Optional[dict], raster_rules: bool = True,
              max_pages: int = v.MAX_PAGES) -> list[PageInput]:
    """Engine input for every page that has text, in page order."""
    entries = _ocr_entries(metadata)
    out: list[PageInput] = []
    document = None
    if pdf_bytes:
        try:
            import pypdfium2 as pdfium

            document = pdfium.PdfDocument(pdf_bytes)
        except Exception:  # noqa: BLE001 - an unreadable PDF falls back to the stored OCR
            logger.warning("tables.pdf_unreadable", exc_info=True)
            document = None
    try:
        count = len(document) if document is not None else (max(entries) if entries else 0)
        for number in range(1, min(count, max_pages) + 1):
            entry = entries.get(number)
            page_input: Optional[PageInput] = None
            if entry is not None and entry.get("ocr_applied") and entry.get("blocks"):
                page_input = page_from_ocr(entry)
                if page_input is not None and document is not None and raster_rules:
                    page_input.rules = _raster_rules(document[number - 1], page_input.width)
            elif document is not None:
                try:
                    page_input = page_from_pdf(document[number - 1], number)
                except Exception:  # noqa: BLE001 - one bad page must not lose the document
                    logger.warning("tables.page_unreadable", extra={"page": number}, exc_info=True)
            elif entry is not None and entry.get("blocks"):
                page_input = page_from_ocr(entry)
            if page_input is not None and page_input.tokens:
                out.append(page_input)
    finally:
        if document is not None:
            document.close()
    return out


def _raster_rules(pdf_page: Any, frame_width: float) -> list:
    """Ruling lines of a scanned page, in the stored OCR blocks' pixel space."""
    try:
        bitmap = pdf_page.render(scale=RULE_RENDER_DPI / 72.0, grayscale=True)
        gray = bitmap.to_numpy()
        if gray.ndim == 3:
            gray = gray[:, :, 0]
        px_to_frame = frame_width / max(gray.shape[1], 1)
        return rules_from_bitmap(gray, px_to_frame=px_to_frame)
    except Exception:  # noqa: BLE001 - no rules is the stream path, not a failure
        logger.warning("tables.raster_rules_failed", exc_info=True)
        return []


__all__ = ["pages_for"]

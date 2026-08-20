"""ARCH-12 Step 5 (F7) — bounding boxes for digital PDFs.

THE GAP THIS CLOSES
===================

`paddle._extract_pdf` builds `OCRPage(text=layer, blocks=[])` for any page
whose text layer is long enough to skip OCR. `DocumentPage.block_spans()`
sees an empty block list, returns `BBOX_NO_BLOCKS`, and `_bbox_for_span`
returns None. Result: **every scanned page has boxes and no digital page
does**, which is the exact inverse of what a customer expects, because
digital PDFs are the majority of what gets uploaded.

`pypdfium2` is already a dependency — it is what rasterises scanned pages —
and its textpage gives per-rectangle geometry. Grouping those rectangles into
lines and emitting them as `OCRBlock`s closes F7 with no schema change and no
new dependency.

THE INVARIANT THAT MAKES IT WORK
================================

`DocumentPage.block_spans()` maps each block onto a character span by
searching for the block's text inside the page text. That search only
succeeds reliably if the page text *is* the join of the block texts. So this
module returns both together, and the page text is constructed as
`"\\n".join(block.text)` — the same relationship `OCRPage` already has for
scanned pages. It is not a coincidence that the two paths now agree; it is the
requirement.

This does mean digital page text changes from `pypdf.extract_text()` output
to pypdfium2 rect-joined output. That is a deliberate, one-directional
change, and it is why F7 is closed **now** rather than later: boxes are
captured at chunk time, so doing this after the corpus grows means a second
backfill over a larger corpus, and the page text changing under existing
chunks would invalidate their `page_start_char` offsets.

COORDINATE SPACE
================

PDF user space has its origin bottom-left with y increasing upward, in points
(1/72 inch). `OCRBlock.box` for scanned pages is in raster pixels at
`RASTER_DPI`, origin top-left. Emitting points here would produce a corpus
where `bbox.space` is `"pixels"` on some rows and silently means points on
others — and a viewer that highlighted a 200-DPI scan correctly would draw a
digital page's box at roughly a third scale in the wrong half of the page.

So the conversion is done here, once:

    scale = RASTER_DPI / 72
    x0_px = left  * scale
    x1_px = right * scale
    y0_px = (page_height_pt - top)    * scale
    y1_px = (page_height_pt - bottom) * scale

WHY CONFIDENCE IS 1.0
=====================

There is no recognition step. The characters are in the file. Reporting a
made-up confidence below 1.0 would make `OCRResult.mean_confidence` — which
ARCH-10 uses for quality routing — read as though a clean digital PDF were a
marginal scan.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Optional, Sequence

from app.services.ocr.base import BoundingBox, OCRBlock

logger = logging.getLogger("app.services.ocr.pdf_text_layer")

#: Must match `paddle.RASTER_DPI`. Imported lazily in `extract` to avoid a
#: circular import; duplicated here as the default so this module is testable
#: standalone.
DEFAULT_RASTER_DPI = 200

POINTS_PER_INCH = 72.0

#: Rectangles narrower or shorter than this in points are separators, rules,
#: and stray glyph fragments. Including them adds blocks whose text is empty
#: or a single punctuation mark, which pollutes the span mapping.
MIN_RECT_POINTS = 1.0


@dataclass(frozen=True)
class TextLayerPage:
    """One digital page, boxed.

    `text` is the newline-join of `blocks`. Callers must not reconstruct it
    any other way — see the invariant in the module docstring.
    """

    text: str
    blocks: list[OCRBlock]
    width: int
    height: int

    @property
    def character_count(self) -> int:
        return len(self.text)

    @property
    def boxed_block_count(self) -> int:
        return sum(1 for block in self.blocks if block.box is not None)


def _rect_count(textpage: Any) -> int:
    """`count_rects` signature drifted across pypdfium2 majors."""
    for args in ((), (0, -1)):
        try:
            return int(textpage.count_rects(*args))
        except TypeError:
            continue
        except Exception:  # noqa: BLE001
            return 0
    return 0


def _bounded_text(
    textpage: Any, *, left: float, bottom: float, right: float, top: float
) -> str:
    """Text inside a rectangle, across the two API spellings."""
    for name in ("get_text_bounded", "get_text_range_bounded", "get_text"):
        method = getattr(textpage, name, None)
        if method is None:
            continue
        try:
            return method(left=left, bottom=bottom, right=right, top=top) or ""
        except TypeError:
            continue
        except Exception:  # noqa: BLE001
            return ""
    return ""


def extract_page(
    page: Any, *, raster_dpi: int = DEFAULT_RASTER_DPI
) -> Optional[TextLayerPage]:
    """Extract line-level text and boxes from one `pypdfium2` page.

    Returns None when the page has no usable text layer, which is the signal
    for the caller to fall back to rasterisation and OCR.
    """
    scale = float(raster_dpi) / POINTS_PER_INCH

    try:
        page_width_pt = float(page.get_width())
        page_height_pt = float(page.get_height())
    except Exception:  # noqa: BLE001
        logger.warning("ocr.text_layer_page_geometry_failed", exc_info=True)
        return None

    textpage = None
    try:
        textpage = page.get_textpage()
    except Exception:  # noqa: BLE001
        logger.warning("ocr.text_layer_unavailable", exc_info=True)
        return None

    blocks: list[OCRBlock] = []
    try:
        total = _rect_count(textpage)
        for index in range(total):
            try:
                left, bottom, right, top = textpage.get_rect(index)
            except Exception:  # noqa: BLE001
                continue

            if (right - left) < MIN_RECT_POINTS or (top - bottom) < MIN_RECT_POINTS:
                continue

            raw = _bounded_text(
                textpage, left=left, bottom=bottom, right=right, top=top
            )
            text = (raw or "").replace("\r\n", " ").replace("\n", " ").strip()
            if not text:
                continue

            box = BoundingBox(
                x0=left * scale,
                y0=(page_height_pt - top) * scale,
                x1=right * scale,
                y1=(page_height_pt - bottom) * scale,
            )
            blocks.append(
                OCRBlock(
                    text=text,
                    confidence=1.0,
                    box=box,
                    polygon=[
                        [box.x0, box.y0],
                        [box.x1, box.y0],
                        [box.x1, box.y1],
                        [box.x0, box.y1],
                    ],
                )
            )
    finally:
        close = getattr(textpage, "close", None)
        if callable(close):
            try:
                close()
            except Exception:  # noqa: BLE001
                pass

    if not blocks:
        return None

    # THE INVARIANT. Do not change this join without changing
    # DocumentPage.block_spans() to match.
    text = "\n".join(block.text for block in blocks)

    return TextLayerPage(
        text=text,
        blocks=blocks,
        width=int(round(page_width_pt * scale)),
        height=int(round(page_height_pt * scale)),
    )


def verify_span_mapping(page_text: str, blocks: Sequence[OCRBlock]) -> bool:
    """Assert that `DocumentPage.block_spans()` will succeed on this page.

    Used by the test suite and by `scripts/verify_arch12.py`. A page that
    fails this produces `BBOX_SPAN_MISMATCH` at chunk time, which means boxes
    are silently dropped for that document — the exact failure this step
    exists to remove, reintroduced by a formatting change.
    """
    cursor = 0
    for block in blocks:
        if not block.text:
            continue
        found = page_text.find(block.text, cursor)
        if found < 0:
            return False
        cursor = found + len(block.text)
    return True


__all__ = [
    "DEFAULT_RASTER_DPI",
    "MIN_RECT_POINTS",
    "POINTS_PER_INCH",
    "TextLayerPage",
    "extract_page",
    "verify_span_mapping",
]
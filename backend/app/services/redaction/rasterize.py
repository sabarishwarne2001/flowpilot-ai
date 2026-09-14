"""ARCH-32 — render, burn, verify. Pure: bytes in, pixels out.

THE ONE RULE THIS MODULE EXISTS TO ENFORCE
==========================================

After `burn_page`, every pixel inside every requested rectangle holds the fill
value and nothing else. Not "mostly". Not "a rectangle was drawn there". The
verification step reads the burned array back and asserts uniformity, because
the difference between a redaction and a decoration is exactly whether the
original pixels are still underneath, and an alpha value of 0.9 looks
identical to 1.0 on a screen while leaving the glyph recoverable by contrast
stretching. `verify_arch32.py` mutates the burn to 90% opacity precisely to
prove this check kills it.

ANNOTATIONS AND FORM FIELDS ARE NOT RENDERED, DELIBERATELY
==========================================================

`draw_annots=False` and `may_draw_forms=False` on every render call.

This is a real trade-off and it is made knowingly. A signature stamp, a
reviewer's sticky note and a filled form field are all annotation content;
rendering them would put their pixels on the page, and the detector has no
text for them (they are not in `document_chunks`, which carries the page
content stream's text). They would therefore survive as *visible, OCR-able
text that nothing ever offered the reviewer a box for* — a leak with a black
rectangle two inches away from it.

Not rendering them means they are absent from the output entirely, which is
what §3.3 promises and what the leak-check gate asserts. The cost is that a
legitimate annotation disappears. The studio's apply dialog states this in
words rather than leaving the operator to discover it, and the manifest
records `annotations_rendered: false` so the decision is on the artifact.

COORDINATES
===========

Regions are stored in PDF points with the origin at the BOTTOM-left, because
that is the space `work_items.extraction_metadata` boxes and every PDF
consumer agree on. Pixels have the origin at the TOP-left. The conversion is
in one function, `box_to_pixels`, and the rounding is deliberately
asymmetric — floor the start, ceil the end — so a box can only ever grow by
rounding, never shrink. A box that shrinks by one pixel under rounding leaves
a one-pixel sliver of the original glyph, which is enough for a human and
more than enough for OCR.
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from typing import Iterable, Optional, Sequence

import numpy as np

from app.services.redaction.vocabulary import (
    BOX_PADDING_POINTS,
    MAX_RENDER_DPI,
    MIN_RENDER_DPI,
)

__all__ = [
    "Box",
    "PixelRect",
    "RenderedPage",
    "BurnResult",
    "RasterizeError",
    "UniformFillError",
    "POINTS_PER_INCH",
    "FILL_VALUE",
    "box_to_pixels",
    "render_pages",
    "burn_page",
    "verify_uniform_fill",
]

POINTS_PER_INCH: float = 72.0

#: Opaque black. Not configurable. A tenant-chosen fill colour is a tenant
#: able to choose a fill whose contrast against the page is low enough to
#: read, and there is no version of that setting worth the support ticket.
FILL_VALUE: int = 0


class RasterizeError(RuntimeError):
    """The source could not be rendered. Never carries page content."""


class UniformFillError(RasterizeError):
    """A burned rectangle was not uniformly filled after burning.

    This is the failure that must never be swallowed. It means the array the
    assembler is about to embed still holds original pixels inside a box the
    operator approved, and shipping it would produce a file that looks
    redacted and is not.
    """


@dataclass(frozen=True)
class Box:
    """A rectangle in PDF points, origin bottom-left. `x1 > x0`, `y1 > y0`."""

    page_number: int
    x0: float
    y0: float
    x1: float
    y1: float

    def padded(self, points: float) -> "Box":
        return Box(
            page_number=self.page_number,
            x0=self.x0 - points,
            y0=self.y0 - points,
            x1=self.x1 + points,
            y1=self.y1 + points,
        )

    @property
    def is_ordered(self) -> bool:
        return self.x1 > self.x0 and self.y1 > self.y0


@dataclass(frozen=True)
class PixelRect:
    """A half-open rectangle in pixels, origin top-left: rows [top, bottom)."""

    left: int
    top: int
    right: int
    bottom: int

    @property
    def is_empty(self) -> bool:
        return self.right <= self.left or self.bottom <= self.top

    @property
    def area(self) -> int:
        return max(0, self.right - self.left) * max(0, self.bottom - self.top)


@dataclass
class RenderedPage:
    """One page as pixels, with the geometry needed to map boxes onto it."""

    page_number: int
    pixels: np.ndarray  # (H, W) uint8 grayscale, or (H, W, 3) uint8 RGB
    width_points: float
    height_points: float
    dpi: int

    @property
    def height_px(self) -> int:
        return int(self.pixels.shape[0])

    @property
    def width_px(self) -> int:
        return int(self.pixels.shape[1])

    @property
    def is_grayscale(self) -> bool:
        return self.pixels.ndim == 2


@dataclass
class BurnResult:
    """A page after burning, plus exactly which pixels were overwritten."""

    page: RenderedPage
    rects: tuple[PixelRect, ...]
    #: Boxes that landed entirely outside the page after conversion. Not an
    #: error — a manual region drawn on a preview of a page that was later
    #: re-extracted at a different size can legitimately fall off — but
    #: counted, because a job where every box misses is a job that redacted
    #: nothing and would otherwise pass the leak check trivially.
    dropped: int = 0


def box_to_pixels(
    box: Box,
    *,
    width_points: float,
    height_points: float,
    width_px: int,
    height_px: int,
) -> PixelRect:
    """Convert a bottom-left points box to a top-left pixel rect, clamped.

    Rounding is asymmetric on purpose: `floor` the leading edges, `ceil` the
    trailing ones. Under any scale the returned rect is a superset of the
    true rectangle, never a subset.
    """
    if width_points <= 0 or height_points <= 0:
        raise RasterizeError("page has non-positive dimensions")

    scale_x = width_px / width_points
    scale_y = height_px / height_points

    left = int(np.floor(box.x0 * scale_x))
    right = int(np.ceil(box.x1 * scale_x))
    # y is measured up from the bottom in points and down from the top in
    # pixels, so the box's TOP edge (y1) becomes the SMALLER pixel row.
    top = int(np.floor((height_points - box.y1) * scale_y))
    bottom = int(np.ceil((height_points - box.y0) * scale_y))

    left = max(0, min(left, width_px))
    right = max(0, min(right, width_px))
    top = max(0, min(top, height_px))
    bottom = max(0, min(bottom, height_px))

    return PixelRect(left=left, top=top, right=right, bottom=bottom)


def render_pages(
    pdf_bytes: bytes,
    *,
    dpi: int,
    pages: Optional[Sequence[int]] = None,
    grayscale: bool = True,
) -> list[RenderedPage]:
    """Render the source to pixel arrays. `pages` is 1-based, None means all.

    The returned arrays are copies. `PdfBitmap.to_numpy()` is a view over a
    buffer owned by the bitmap, and the bitmap is closed here — returning the
    view would hand the caller memory that pdfium has already released, which
    fails as corrupted pixels rather than as a crash.
    """
    if not MIN_RENDER_DPI <= dpi <= MAX_RENDER_DPI:
        raise RasterizeError(
            f"dpi {dpi} outside the supported range "
            f"{MIN_RENDER_DPI}-{MAX_RENDER_DPI}"
        )
    if not pdf_bytes:
        raise RasterizeError("empty source document")

    import pypdfium2 as pdfium  # local: keeps the module importable without it

    scale = dpi / POINTS_PER_INCH
    rendered: list[RenderedPage] = []

    document = pdfium.PdfDocument(io.BytesIO(pdf_bytes))
    try:
        total = len(document)
        wanted = list(pages) if pages is not None else list(range(1, total + 1))
        for page_number in wanted:
            if not 1 <= page_number <= total:
                raise RasterizeError(
                    f"page {page_number} is outside the document's 1-{total}"
                )
            page = document[page_number - 1]
            width_points, height_points = page.get_size()
            bitmap = page.render(
                scale=scale,
                grayscale=grayscale,
                # See the module header. These two flags are the difference
                # between "annotations are gone" and "annotations are gone
                # except the visible ones".
                draw_annots=False,
                may_draw_forms=False,
            )
            try:
                array = np.array(bitmap.to_numpy(), copy=True, dtype=np.uint8)
            finally:
                bitmap.close()
            rendered.append(
                RenderedPage(
                    page_number=page_number,
                    pixels=array,
                    width_points=float(width_points),
                    height_points=float(height_points),
                    dpi=dpi,
                )
            )
    finally:
        document.close()

    return rendered


def burn_page(
    page: RenderedPage,
    boxes: Iterable[Box],
    *,
    padding_points: float = BOX_PADDING_POINTS,
    fill_value: int = FILL_VALUE,
    verify: bool = True,
) -> BurnResult:
    """Overwrite every box with `fill_value`, then prove it.

    The assignment is `array[...] = fill_value`, not a blend. There is no
    alpha parameter and no compositing step, because the only correct opacity
    is 1.0 and a parameter that can hold 0.9 is a parameter that eventually
    does.
    """
    if page.pixels.dtype != np.uint8:
        raise RasterizeError(
            f"page {page.page_number} pixels are {page.pixels.dtype}, expected uint8"
        )

    pixels = page.pixels
    rects: list[PixelRect] = []
    dropped = 0

    for box in boxes:
        if box.page_number != page.page_number:
            continue
        if not box.is_ordered:
            raise RasterizeError(
                f"box on page {box.page_number} is not ordered (x1>x0, y1>y0)"
            )
        rect = box_to_pixels(
            box.padded(padding_points),
            width_points=page.width_points,
            height_points=page.height_points,
            width_px=page.width_px,
            height_px=page.height_px,
        )
        if rect.is_empty:
            dropped += 1
            continue
        pixels[rect.top : rect.bottom, rect.left : rect.right] = fill_value
        rects.append(rect)

    burned = BurnResult(
        page=RenderedPage(
            page_number=page.page_number,
            pixels=pixels,
            width_points=page.width_points,
            height_points=page.height_points,
            dpi=page.dpi,
        ),
        rects=tuple(rects),
        dropped=dropped,
    )

    if verify:
        verify_uniform_fill(burned, fill_value=fill_value)
    return burned


def verify_uniform_fill(result: BurnResult, *, fill_value: int = FILL_VALUE) -> None:
    """Assert every burned rectangle is uniformly `fill_value`. Raise if not.

    Reads the array back rather than trusting the write. The write is a NumPy
    slice assignment and will not silently fail today — but the burn is the
    single load-bearing operation in the phase, and a check that costs one
    comparison per burned pixel is cheap next to a customer discovering the
    answer.
    """
    pixels = result.page.pixels
    for rect in result.rects:
        window = pixels[rect.top : rect.bottom, rect.left : rect.right]
        if window.size == 0:
            raise UniformFillError(
                f"page {result.page.page_number}: a recorded rectangle is empty"
            )
        if not bool(np.all(window == fill_value)):
            distinct = int(np.unique(window).size)
            raise UniformFillError(
                f"page {result.page.page_number}: rectangle "
                f"({rect.left},{rect.top})-({rect.right},{rect.bottom}) holds "
                f"{distinct} distinct values, expected 1 ({fill_value}). "
                "Original pixels survive inside an approved region."
            )


def page_dimensions(pdf_bytes: bytes) -> list[tuple[int, float, float]]:
    """(page_number, width_points, height_points) for every page. No rendering.

    Used by detection to map block boxes without paying for a raster, and by
    the studio's region validator to refuse a rectangle drawn off the page.
    """
    import pypdfium2 as pdfium

    document = pdfium.PdfDocument(io.BytesIO(pdf_bytes))
    try:
        sizes: list[tuple[int, float, float]] = []
        for index in range(len(document)):
            width, height = document[index].get_size()
            sizes.append((index + 1, float(width), float(height)))
        return sizes
    finally:
        document.close()
"""ARCH-32 — build a NEW PDF from burned pixels. Nothing is copied from source.

THE INVARIANT
=============

This module never opens the source document. It cannot: its only inputs are
NumPy arrays and page dimensions. That is not a convenience, it is the whole
argument — "no hidden object carries the original" is a claim about the
assembler's inputs, and the cheapest way to make it true is to give the
assembler nothing it could leak.

`verify_arch32.py` mutates this file to copy a source page object and requires
the gate to kill it. The gate that kills it is not a source-text grep; it
renders a synthetic PDF carrying text in six hiding places, assembles, and
asserts the output's object tree contains none of them.

WHY EACH PAGE IS ONE IMAGE AND NOT A TILE GRID
==============================================

Tiling would cap peak memory on a very large page. It would also introduce
seams, and a seam is a row of pixels whose provenance is "the join", which is
the one place a reader cannot tell a rendering artefact from an incomplete
burn. One image per page keeps the mapping between "what was verified" and
"what was embedded" exactly one-to-one.

DETERMINISM
===========

Two runs of the same job must produce identical bytes, because the output
hash is what the manifest attests to and a hash that changes on a re-run
attests to nothing. Three things are therefore pinned:

  * `/Info` holds a fixed producer string and nothing else. No CreationDate,
    no ModDate — a timestamp is the usual reason "the same PDF" has two
    hashes.
  * No XMP. `pikepdf` will happily synthesise an XMP packet containing a
    timestamp and a document UUID the moment anything asks for metadata; the
    assembler never asks, and `leakcheck` fails the job if `/Metadata` shows
    up anyway.
  * `deterministic_id=True` on save, which derives the trailer `/ID` from the
    file's content rather than from the clock and a random seed.

Flate is used at a fixed level for the same reason: zlib's output is stable
for a given level and input, and "whichever level the library defaults to
this release" is not.
"""

from __future__ import annotations

import io
import zlib
from dataclasses import dataclass
from typing import Sequence

import numpy as np

from app.services.redaction.rasterize import RenderedPage
from app.services.redaction.vocabulary import PRODUCER_STRING

__all__ = [
    "AssembleError",
    "AssembledDocument",
    "FLATE_LEVEL",
    "assemble_pdf",
]


class AssembleError(RuntimeError):
    """The output document could not be built."""


#: Fixed, not `zlib.Z_DEFAULT_COMPRESSION`. See the header on determinism.
FLATE_LEVEL: int = 6


@dataclass(frozen=True)
class AssembledDocument:
    pdf_bytes: bytes
    page_count: int
    #: Bytes of image data before compression, for the manifest's size note.
    raw_image_bytes: int


def _image_stream(pdf, array: np.ndarray):
    """One image XObject from one page's pixels."""
    import pikepdf

    if array.dtype != np.uint8:
        raise AssembleError(f"page pixels are {array.dtype}, expected uint8")

    if array.ndim == 2:
        colorspace = pikepdf.Name.DeviceGray
        height, width = array.shape
    elif array.ndim == 3 and array.shape[2] == 3:
        colorspace = pikepdf.Name.DeviceRGB
        height, width, _ = array.shape
    else:
        raise AssembleError(
            f"unsupported pixel shape {array.shape}; expected (H,W) or (H,W,3)"
        )

    if width <= 0 or height <= 0:
        raise AssembleError("page has no pixels")

    payload = np.ascontiguousarray(array).tobytes()

    # `Stream(pdf, data)` sets the stream's DECODED content and leaves the
    # encoding to `save()`. That is the wrong half of the contract here: it
    # makes the stored bytes a function of whatever pikepdf's compressor does
    # this release, and `compress_streams=False` then stores 8 MB of raw
    # pixels per page. `write(..., filter=...)` states that the bytes are
    # already Flate-encoded, so what lands in the file is exactly what
    # `zlib.compress` produced at a level this module pins.
    stream = pikepdf.Stream(pdf, b"")
    stream.write(
        zlib.compress(payload, FLATE_LEVEL), filter=pikepdf.Name.FlateDecode
    )
    stream.Type = pikepdf.Name.XObject
    stream.Subtype = pikepdf.Name.Image
    stream.Width = int(width)
    stream.Height = int(height)
    stream.ColorSpace = colorspace
    stream.BitsPerComponent = 8
    return stream, len(payload)


def assemble_pdf(pages: Sequence[RenderedPage]) -> AssembledDocument:
    """Build a complete PDF whose every page is one image and nothing else.

    `pages` must already be burned and verified. This function does not check
    for un-redacted content: it has no idea what the content was. The check
    that the output is clean is `leakcheck.check_output`, which runs on these
    bytes afterwards and can therefore catch an assembler bug as well as a
    burn bug.
    """
    import pikepdf

    if not pages:
        raise AssembleError("cannot assemble a document with no pages")

    pdf = pikepdf.Pdf.new()
    raw_total = 0

    for page in pages:
        if page.width_points <= 0 or page.height_points <= 0:
            raise AssembleError(
                f"page {page.page_number} has non-positive dimensions"
            )

        image, raw_bytes = _image_stream(pdf, page.pixels)
        raw_total += raw_bytes

        # The content stream places the image over the whole MediaBox. The
        # `cm` matrix is the page size because a PDF image XObject is always
        # drawn into the unit square; scaling it here rather than in the
        # image is what keeps the pixel data an exact copy of what was
        # verified.
        width = float(page.width_points)
        height = float(page.height_points)
        content = f"q {width:.4f} 0 0 {height:.4f} 0 0 cm /Im0 Do Q".encode("ascii")

        pdf_page = pikepdf.Dictionary(
            Type=pikepdf.Name.Page,
            MediaBox=[0, 0, width, height],
            Resources=pikepdf.Dictionary(
                XObject=pikepdf.Dictionary(Im0=pdf.make_indirect(image))
            ),
            Contents=pdf.make_indirect(pikepdf.Stream(pdf, content)),
        )
        pdf.pages.append(pikepdf.Page(pdf.make_indirect(pdf_page)))

    # `/Info` is set to exactly one key. Assigning a fresh dictionary rather
    # than editing the existing one means a key pikepdf seeded cannot survive.
    pdf.docinfo = pdf.make_indirect(
        pikepdf.Dictionary(Producer=pikepdf.String(PRODUCER_STRING))
    )

    buffer = io.BytesIO()
    pdf.save(
        buffer,
        deterministic_id=True,
        # `compress_streams=False` alone is a trap: QPDF's default
        # `stream_decode_level` is `generalized`, which DECODES Flate on the
        # way out. The first cut of this module shipped 8.4 MB of raw pixels
        # per page and looked correct in every other respect — the leak check
        # passed, the hash was stable, and only the file size said anything
        # was wrong. `none` is what keeps the bytes we encoded.
        compress_streams=False,
        stream_decode_level=pikepdf.StreamDecodeLevel.none,
        # Nothing here is PDF/A and nothing should acquire an XMP packet on
        # the way to disk. `leakcheck` fails the job on `/Metadata`, so these
        # two are belt and braces rather than the only defence.
        preserve_pdfa=False,
        fix_metadata_version=False,
        linearize=False,
    )
    pdf.close()

    data = buffer.getvalue()
    if not data.startswith(b"%PDF-"):
        raise AssembleError("assembled output is not a PDF")

    return AssembledDocument(
        pdf_bytes=data,
        page_count=len(pages),
        raw_image_bytes=raw_total,
    )
"""F-066: every PDFium call site holds the process lock, not only packet thumbnails.

F-050 put `PDFIUM_LOCK` around packet thumbnails. The other places that open a
PDF with pypdfium2 — the table reader (run inline by the API's "extract
tables" route), the PDF text layer and page rendering in OCR, redaction's
rasterising and leak check, the corroborator's synthetic pages and its
page-render route — did not take it. Any two of them on two threads of one
process (the API's thread pool) can corrupt PDFium's heap and abort the
process for every tenant.

The stress runs each call site on 8 threads, interleaved with packet
thumbnails, in a child process: a crash there fails this test instead of
killing pytest.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from tests.services.test_packet_thumbnail_thread_safety import BACKEND


def _pdf_with_pages(pages: int) -> bytes:
    """Four text pages long enough for the OCR text-layer path (no OCR engine needed)."""
    from tests.engines.conftest import make_pdf

    return make_pdf([[f"Page {n} of {pages} - ACME SUPPLIES LTD statement of account for Contoso Retail",
                      "Item  Qty  Rate  Amount", "Steel bolts M8  100  2.50  250.00", "Total due  250.00 USD"]
                     for n in range(1, pages + 1)])

STRESS = textwrap.dedent(
    """
    import io, sys, threading
    from types import SimpleNamespace
    import app.core.storage as storage
    from app.services.packets import service as packets
    from app.services.tables import reader
    from pathlib import Path
    from app.services.redaction import rasterize, leakcheck
    from app.services.corroboration import synthetic
    from app.services.ocr.paddle import PaddleOCRProvider
    provider = PaddleOCRProvider(language="en")  # the engine is built lazily; text-layer pages never build it

    pdf = open(sys.argv[1], "rb").read()
    which = sys.argv[2]
    class _Storage:
        def get(self, _key):
            return pdf
    storage.get_storage_driver = lambda: _Storage()
    item = SimpleNamespace(file_type="application/pdf", stored_filename="packet.pdf")
    errors = []

    def call_site():
        if which == "tables":
            assert reader.pages_for(pdf_bytes=pdf, metadata={}, raster_rules=True), "no pages read"
        elif which == "redaction_render":
            assert len(rasterize.render_pages(pdf, dpi=150, pages=[1, 2])) == 2
        elif which == "redaction_sizes":
            assert len(rasterize.page_dimensions(pdf)) == 4
        elif which == "leakcheck":
            assert len(leakcheck.extract_text_per_page(pdf)) == 4
        elif which == "corroboration_pages":
            assert len(synthetic.text_layer_pages(pdf)) == 4
        elif which == "ocr_text_layer":
            path = sys.argv[1]
            assert len(provider._extract_pdf(Path(path), max_pages=4)) == 4
        else:
            raise SystemExit("unknown call site " + which)

    def worker(index):
        try:
            for round_ in range(20):
                if index % 2:
                    png = packets.render_thumbnail(item, 1 + round_ % 4)
                    assert png[:8] == b"\\x89PNG\\r\\n\\x1a\\n"
                else:
                    call_site()
        except Exception as exc:  # a Python error is a failure too, not a crash
            errors.append(repr(exc))

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
    for t in threads: t.start()
    for t in threads: t.join()
    print("ERRORS", errors[:3])
    sys.exit(1 if errors else 0)
    """
)


@pytest.mark.parametrize("call_site", ["tables", "redaction_render", "redaction_sizes", "leakcheck",
                                       "corroboration_pages", "ocr_text_layer"])
def test_concurrent_pdfium_call_sites_do_not_crash_the_process(tmp_path: Path, call_site: str) -> None:
    pdf = tmp_path / "doc.pdf"
    pdf.write_bytes(_pdf_with_pages(4))
    script = tmp_path / "stress.py"
    script.write_text(STRESS)
    result = subprocess.run([sys.executable, str(script), str(pdf), call_site], cwd=BACKEND, capture_output=True,
                            text=True, timeout=240, env={**__import__("os").environ, "PYTHONPATH": str(BACKEND)})
    assert result.returncode == 0, (result.returncode, result.stdout[-500:], result.stderr[-1500:])

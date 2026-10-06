"""F-105: every PDFium page is closed inside the lock, never by the garbage collector.

F-050 / F-066 hold `PDFIUM_LOCK` for a document's lifetime. But a pypdfium2
page sits in a reference cycle, so a page that is only dropped (`document[i]
.get_size()`) is not freed when the line ends: Python's cycle collector frees
it later, on whatever thread happens to allocate at that moment, and that
thread does not hold the lock. Its finaliser then closes the page in PDFium
while another thread is inside PDFium, and removes it from the document's
child set while the owner may be iterating it ("Set changed size during
iteration", seen once in the full suite in the 8-thread stress).

The proof: with the collector paused, no call site may reach
`PdfDocument.close()` with a page still open, and no file may index a
`PdfDocument` directly (pages come from `pdfium_page()`, which closes them).
"""

from __future__ import annotations

import ast
import gc
from pathlib import Path
from types import SimpleNamespace

import pypdfium2 as pdfium
import pytest

from tests.services.test_pdfium_call_sites_thread_safety import _pdf_with_pages

APP = Path(__file__).resolve().parents[2] / "app"


@pytest.fixture
def pdf() -> bytes:
    return _pdf_with_pages(4)


@pytest.fixture
def open_pages_at_close(monkeypatch):
    """Pages still open when each document closes, with the cycle collector paused."""
    seen: list[int] = []
    original = pdfium.PdfDocument.close

    def close(self, *args, **kwargs):
        kids = [ref() for ref in list(self._kids)]
        seen.append(sum(1 for kid in kids if kid is not None and kid.raw))
        return original(self, *args, **kwargs)

    monkeypatch.setattr(pdfium.PdfDocument, "close", close)
    was_enabled = gc.isenabled()
    gc.disable()
    try:
        yield seen
    finally:
        if was_enabled:
            gc.enable()


def _thumbnail(pdf: bytes, monkeypatch):
    import app.core.storage as storage
    from app.services.packets import service as packets

    monkeypatch.setattr(storage, "get_storage_driver", lambda: SimpleNamespace(get=lambda _key: pdf))
    item = SimpleNamespace(file_type="application/pdf", stored_filename="packet.pdf")
    return packets.render_thumbnail(item, 2)


def _ocr_text_layer(pdf: bytes, tmp_path: Path):
    from app.services.ocr.paddle import PaddleOCRProvider

    path = tmp_path / "doc.pdf"
    path.write_bytes(pdf)
    return PaddleOCRProvider(language="en")._extract_pdf(path, max_pages=4)


CALL_SITES = {
    "redaction_sizes": lambda pdf, mp, tmp: __import__(
        "app.services.redaction.rasterize", fromlist=["x"]).page_dimensions(pdf),
    "redaction_render": lambda pdf, mp, tmp: __import__(
        "app.services.redaction.rasterize", fromlist=["x"]).render_pages(pdf, dpi=150, pages=[1, 2]),
    "leakcheck": lambda pdf, mp, tmp: __import__(
        "app.services.redaction.leakcheck", fromlist=["x"]).extract_text_per_page(pdf),
    "corroboration_pages": lambda pdf, mp, tmp: __import__(
        "app.services.corroboration.synthetic", fromlist=["x"]).text_layer_pages(pdf),
    "tables": lambda pdf, mp, tmp: __import__(
        "app.services.tables.reader", fromlist=["x"]).pages_for(pdf_bytes=pdf, metadata={}, raster_rules=True),
    "thumbnail": lambda pdf, mp, tmp: _thumbnail(pdf, mp),
    "ocr_text_layer": lambda pdf, mp, tmp: _ocr_text_layer(pdf, tmp),
}


@pytest.mark.parametrize("call_site", sorted(CALL_SITES))
def test_no_page_is_left_open_for_the_garbage_collector(call_site, pdf, open_pages_at_close, monkeypatch,
                                                         tmp_path):
    assert CALL_SITES[call_site](pdf, monkeypatch, tmp_path)
    assert open_pages_at_close, "the call site never closed its document"
    assert open_pages_at_close == [0] * len(open_pages_at_close), (
        f"{call_site}: pages still open when the document closed: {open_pages_at_close}"
    )


def _documents_indexed_directly(tree: ast.AST) -> list[int]:
    documents: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
            func = node.value.func
            if isinstance(func, ast.Attribute) and func.attr == "PdfDocument":
                documents.update(t.id for t in node.targets if isinstance(t, ast.Name))
    return [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name) and node.value.id in documents
    ]


def test_no_file_indexes_a_pdf_document_directly():
    offenders = []
    for path in sorted(APP.rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        if "PdfDocument" not in source:
            continue
        for line in _documents_indexed_directly(ast.parse(source)):
            offenders.append(f"{path.relative_to(APP.parent)}:{line}")
    assert not offenders, "open pages with app.core.pdfium_lock.pdfium_page(): " + ", ".join(offenders)

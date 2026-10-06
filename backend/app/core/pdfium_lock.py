"""F-050. One lock for every PDFium call made inside one process.

PDFium (through pypdfium2) is not thread-safe: two threads using it at the same
time corrupt its heap and the process aborts. The API serves sync routes from a
thread pool, so any route that renders or parses a PDF with pypdfium2 can be
running on several threads at once. Hold this lock for the whole lifetime of
the PdfDocument (open, render, convert, close).

It is reentrant so a helper that takes the lock may call another that also
takes it. It serialises PDFium work within a process only; separate worker
processes each have their own PDFium and their own lock.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

PDFIUM_LOCK = threading.RLock()


@contextmanager
def pdfium_page(document: Any, index: int) -> Iterator[Any]:
    """F-105. Page `index` (0-based) of an open PdfDocument, closed when the block ends.

    A pypdfium2 page sits in a reference cycle, so a page that is only dropped
    is not freed when the line ends: the cycle collector frees it later, on
    whichever thread allocates at that moment and without this lock, and its
    finaliser then calls into PDFium. Open pages with this, inside the lock.
    """
    page = document[index]
    try:
        yield page
    finally:
        page.close()


__all__ = ["PDFIUM_LOCK", "pdfium_page"]

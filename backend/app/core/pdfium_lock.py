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

PDFIUM_LOCK = threading.RLock()

__all__ = ["PDFIUM_LOCK"]

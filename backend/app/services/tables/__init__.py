"""ARCH44-S1:package — the Complex Table & Hierarchical Grid Extractor (capability.table_intelligence).

Pure engine (no I/O):  geometry -> grid -> engine (continuation, typing, roles)
                       -> validate; values (typed cells); roles; export.
Around it:             reader (stored OCR + the PDF text layer), memory
                       (learned column roles), service (the database), gate.
Golden documents:      synthetic (verify_arch44 G-gates).
"""

from app.services.tables import vocabulary

__all__ = ["vocabulary"]

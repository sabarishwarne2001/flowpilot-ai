"""ARCH-32 — zero-leakage geometric PII redaction and PDF sanitization.

DELIBERATELY EMPTY OF RE-EXPORTS
================================

`from app.services.redaction import validators` must not drag `pikepdf`,
`pypdfium2` and NumPy into the importing process. The API layer imports
`vocabulary` to render profile choices; the light worker profile imports
nothing here at all. Re-exporting the engines from this file would make every
one of those imports pull the PDF stack, and `assert_imports_match_profile`
would start failing for a reason that has nothing to do with worker profiles.

Import the submodule you need, by name.

    from app.services.redaction import vocabulary          # stdlib only
    from app.services.redaction import validators          # stdlib only
    from app.services.redaction import manifest            # stdlib only
    from app.services.redaction import rasterize           # + numpy, pypdfium2
    from app.services.redaction import assemble            # + numpy, pikepdf
    from app.services.redaction import leakcheck           # + pikepdf, pypdfium2

LICENSES (ARCH-32 §7, dependency note)
======================================

    pikepdf     MPL-2.0      page assembly and object-tree inspection
    pypdfium2   BSD-3 / Apache-2.0 (PDFium is BSD-3)   rendering, text extraction

Both are permissive or file-level copyleft and impose no obligation on a
closed-source SaaS. PyMuPDF is deliberately NOT used: its AGPL license is
incompatible with this deployment without a commercial license, and a
transitive import of it would be a licensing incident rather than a bug.
"""

from __future__ import annotations

__all__: list[str] = []
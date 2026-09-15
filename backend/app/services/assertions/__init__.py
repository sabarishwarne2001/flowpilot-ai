"""ARCH-33 — Semantic Assertion Automation with Confidence Triage.

DELIBERATELY EMPTY OF EAGER IMPORTS
===================================

`app/services/__init__.py` imports the LLM service, the retriever, the
embedding service and a dozen others at import time. That is a reasonable
arrangement for a package whose members all need a configured application.

This package is the opposite case. `compiler.py`, `families/*`,
`quotecheck.py`, `features.py`, `calibration.py` and `routing.py` are pure by
contract — standard library plus `app/core/normalize.py` — and
`verify_arch33.py` imports them by their real dotted names with no database,
no settings object and no Redis. Re-exporting them here would mean importing
`app.services.assertions.vocabulary` pulls in the compiler, and importing the
compiler from the model layer pulls in the parsers.

So this file declares the package and nothing else. Callers import the module
they actually want:

    from app.services.assertions import vocabulary as vocab
    from app.services.assertions.compiler import compile_sentence
    from app.services.assertions.families import read

The impure members of this package — `retrieve.py`, `evaluate.py`,
`triage.py` — follow the same rule when they arrive: they hold the Session,
and they are imported by name, never from here.
"""

from __future__ import annotations

__all__: list[str] = []
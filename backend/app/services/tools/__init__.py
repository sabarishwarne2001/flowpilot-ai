"""ARCH-12 Step 4 — the far side of the R33 boundary. Empty on purpose.

ARCH-13 puts tool selectors here. This package exists now, empty, for one
reason: `tests/services/test_arch12_isolation.py` walks every file in it with
the `ast` module and fails the build if any of them imports
`app.services.fenced_context`, imports a retrieval service, or annotates a
parameter with a chunk-shaped type.

An empty directory with a test pointed at it is a rule that is already
enforced. A rule written in a design document is a rule that gets discovered
during code review, at the point where somebody has already written the
selector that violates it.

WHAT MAY LIVE HERE
==================

Callables that take the **user's question** and **structured, non-document
arguments** — ids, enum values, dates the user typed — and return an action to
perform. Nothing that has read a document.

WHAT MAY NOT
============

Anything that accepts `FencedContext`, a retrieval result dict, a chunk, a
page of extracted text, or a string that came from one. If a selector needs
to know something a document says, the correct shape is: the model answers
from the document in the generation path, the *user* confirms, and the
confirmed value arrives here as a typed argument.

Registration is via `fenced_context.register_tool_selector`, which refuses at
import time. See that module for why import time rather than call time.
"""

from __future__ import annotations

__all__: list[str] = []

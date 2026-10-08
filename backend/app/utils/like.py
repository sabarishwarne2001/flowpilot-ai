"""Substring search with LIKE / ILIKE, where the user's text is text.

`%` and `_` are LIKE wildcards. A search box that passes its text straight into a pattern makes
"_" match every row and "Procurement_Policy" match "ProcurementXPolicy" (F-156). Use
`contains_pattern(text)` with `escape=LIKE_ESCAPE` on the `like`/`ilike` call.
"""

from __future__ import annotations

#: The escape character `contains_pattern` uses; pass it as `escape=` to `like`/`ilike`.
LIKE_ESCAPE = "\\"


def contains_pattern(text: str) -> str:
    """A LIKE pattern matching `text` literally anywhere in the value."""
    escaped = (
        text.replace(LIKE_ESCAPE, LIKE_ESCAPE * 2)
        .replace("%", f"{LIKE_ESCAPE}%")
        .replace("_", f"{LIKE_ESCAPE}_")
    )
    return f"%{escaped}%"

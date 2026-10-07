"""F-127 — every list paged by offset ends its sort on a unique column.

An `ORDER BY created_at DESC LIMIT n OFFSET m` leaves rows that share a timestamp
(written in one transaction, or one upload burst) in whatever order the database
picks, and it may pick another on the next request: an item shows on two pages or
on none, and a workbench cursor points at a different row after a refetch. That
is how the extraction review queue (`/verifications`) and Run history behaved.

This reads the source, like the other guards here: for each `.offset(...)` in a
method chain that also calls `.order_by(...)`, the last sort key must be an `id`
column (or another column the allow-list below names as unique).
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parents[2] / "app"

#: Sort keys that are unique on their own, so nothing after them is needed.
UNIQUE_KEYS = {"id", "seq", "item_id", "number"}  # invoices.number: uq_invoices_number

pytestmark = pytest.mark.no_db


def _chain(node: ast.AST) -> list[ast.Call]:
    """The calls of one method chain, outermost first: a.b().c().d() -> [d, c, b]."""
    calls: list[ast.Call] = []
    current = node
    while isinstance(current, ast.Call) and isinstance(current.func, ast.Attribute):
        calls.append(current)
        current = current.func.value
    return calls


def _last_key(order_by: ast.Call) -> str | None:
    if not order_by.args:
        return None
    last = order_by.args[-1]
    # x.id.desc() / x.id.asc() / x.c.id.desc().nulls_last()
    while isinstance(last, ast.Call) and isinstance(last.func, ast.Attribute):
        last = last.func.value
    if isinstance(last, ast.Attribute):
        return last.attr
    return None


def _offenders() -> list[str]:
    found: list[str] = []
    for path in sorted(APP.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        seen: set[int] = set()
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or id(node) in seen:
                continue
            calls = _chain(node)
            for call in calls:
                seen.add(id(call))
            names = [call.func.attr for call in calls]  # type: ignore[union-attr]
            if "offset" not in names or "order_by" not in names:
                continue
            order_by = calls[names.index("order_by")]
            if _last_key(order_by) not in UNIQUE_KEYS:
                found.append(f"{path.relative_to(APP.parent)}:{order_by.lineno}")
    return found


def test_every_offset_paged_query_ends_on_a_unique_key() -> None:
    assert _offenders() == []

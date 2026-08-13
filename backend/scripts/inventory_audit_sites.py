#!/usr/bin/env python
"""Enumerate every `AUDIT | ` call site in app/ and emit a reconciliation CSV.

Run BEFORE conversion to re-anchor the A.1.1 baseline, and AFTER conversion to
prove the residue matches the justified non-converted allowlist.

Emits: module, line, event_name (or DYNAMIC), and the enclosing function.
Exit 0 always — this is an inventory, not a gate.
"""

from __future__ import annotations

import ast
import csv
import re
import sys
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1] / "app"
OUTPUT = Path(__file__).resolve().parents[1] / "arch07_audit_inventory.csv"

AUDIT_LITERAL = re.compile(r"AUDIT\s*\|\s*([A-Z0-9_]+)\s*\|")
AUDIT_MARKER = re.compile(r"AUDIT\s*\|")


def _enclosing_function(tree: ast.Module, lineno: int) -> str:
    best = ("<module>", -1)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.lineno <= lineno and node.lineno > best[1]:
                end = getattr(node, "end_lineno", node.lineno)
                if lineno <= end:
                    best = (node.name, node.lineno)
    return best[0]


def main() -> int:
    rows: list[dict[str, str]] = []
    for path in sorted(APP_ROOT.rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        if "AUDIT" not in source:
            continue
        try:
            tree = ast.parse(source)
        except SyntaxError:
            print(f"[WARN] unparseable: {path}", file=sys.stderr)
            tree = ast.parse("")

        for index, line in enumerate(source.splitlines(), start=1):
            if not AUDIT_MARKER.search(line):
                continue
            match = AUDIT_LITERAL.search(line)
            rows.append(
                {
                    "module": str(path.relative_to(APP_ROOT.parent)),
                    "line": str(index),
                    "event": match.group(1) if match else "DYNAMIC",
                    "function": _enclosing_function(tree, index),
                    "source": line.strip()[:160],
                }
            )

    with OUTPUT.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle, fieldnames=["module", "line", "event", "function", "source"]
        )
        writer.writeheader()
        writer.writerows(rows)

    dynamic = [r for r in rows if r["event"] == "DYNAMIC"]
    distinct = {r["event"] for r in rows if r["event"] != "DYNAMIC"}

    print(f"call sites          : {len(rows)}")
    print(f"distinct event names: {len(distinct)}")
    print(f"DYNAMIC (unresolvable statically): {len(dynamic)}")
    for row in dynamic:
        print(f"  {row['module']}:{row['line']}  in {row['function']}()")
    print(f"\nwritten: {OUTPUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
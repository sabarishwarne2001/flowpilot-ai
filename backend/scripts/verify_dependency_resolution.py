"""Gate: every callable-class dependency must expose a real __globals__.

    python scripts/verify_dependency_resolution.py
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
MIXIN = "ResolvableDependency"


def main() -> int:
    findings: list[tuple[str, str, list[str]]] = []
    protected = 0

    for path in sorted((BACKEND / "app").rglob("*.py")):
        relative = path.relative_to(BACKEND).as_posix()
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue

        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            calls = [
                m for m in node.body
                if isinstance(m, ast.FunctionDef) and m.name == "__call__"
            ]
            if not calls:
                continue
            annotated = [
                a.arg for a in calls[0].args.args
                if a.annotation is not None and a.arg != "self"
            ]
            if not annotated:
                continue

            bases = [b.id for b in node.bases if isinstance(b, ast.Name)]
            if MIXIN in bases or node.name == MIXIN:
                protected += 1
                continue
            findings.append((relative, node.name, annotated))

    print(f"Callable-class dependencies with annotated __call__: {protected + len(findings)}")
    print(f"  protected by {MIXIN}: {protected}")
    print(f"  unprotected:            {len(findings)}")

    if findings:
        print("\n--- UNPROTECTED: these become phantom query params under PEP 563 ---")
        for relative, name, annotated in findings:
            print(f"  {relative}  class {name}  params: {annotated}")
        print(
            f"\nGATE FAILED: {len(findings)} class(es) must inherit {MIXIN}.",
            file=sys.stderr,
        )
        return 1

    print(f"\n[OK] Every callable-class dependency inherits {MIXIN}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

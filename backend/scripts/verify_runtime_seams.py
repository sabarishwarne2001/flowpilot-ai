"""Static verification gate for runtime seam remediation (SEAM-1 through SEAM-4)."""

from __future__ import annotations

import ast
import sys
from pathlib import Path
from typing import Callable

BACKEND = Path(__file__).resolve().parent.parent

CHECKS: list[tuple[str, Callable[[], None]]] = []


def check(label: str) -> Callable[[Callable[[], None]], Callable[[], None]]:
    def decorate(fn: Callable[[], None]) -> Callable[[], None]:
        CHECKS.append((label, fn))
        return fn
    return decorate


def _read(relative: str) -> str:
    path = BACKEND / relative
    assert path.exists(), f"missing file: {relative}"
    return path.read_text(encoding="utf-8")


@check("S1  outbox_service uses SAVEPOINT deduplication on duplicate idempotency keys")
def _s1() -> None:
    source = _read("app/services/outbox_service.py")
    assert "_existing_by_idempotency_key" in source, "Missing _existing_by_idempotency_key helper"
    assert "begin_nested()" in source, "emit() must use db.begin_nested() to prevent poisoning outer transactions"
    assert "IntegrityError" in source, "emit() must catch IntegrityError on raced duplicate inserts"


@check("S2  job_service uses SAVEPOINT deduplication on duplicate job keys")
def _s2() -> None:
    source = _read("app/services/job_service.py")
    assert "_existing_job_by_idempotency_key" in source, "Missing _existing_job_by_idempotency_key helper"
    assert "begin_nested()" in source, "enqueue() must use db.begin_nested() to prevent poisoning outer transactions"
    assert "IntegrityError" in source, "enqueue() must catch IntegrityError on raced duplicate inserts"


@check("S3  enrich handler auto-provisions defaults on missing settings")
def _s3() -> None:
    source = _read("app/workers/handlers/enrich.py")
    assert "_ensure_workspace_defaults" in source, "enrich.py must auto-provision workspace defaults"
    assert "raise ValueError(f\"No AI settings" not in source, "enrich.py must not raise on absent settings"


@check("S4  enrich handler _guarded catches generic exceptions during metadata extraction")
def _s4() -> None:
    tree = ast.parse(_read("app/workers/handlers/enrich.py"))
    guarded_found = False
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_guarded":
            handlers = [h for h in ast.walk(node) if isinstance(h, ast.ExceptHandler)]
            caught = [ast.unparse(h.type) for h in handlers if h.type is not None]
            assert "Exception" in caught, "_guarded must catch generic Exception to prevent LLM quirks from killing ingestion"
            guarded_found = True
    assert guarded_found, "Could not locate _guarded function in enrich.py"


@check("S5  llm_service._extract_json handles regex markdown blocks safely")
def _s5() -> None:
    source = _read("app/services/llm_service.py")
    assert "re.search" in source or "find(\"{\")" in source, "llm_service must extract JSON using substring/regex matching"


def main() -> int:
    failures = 0
    for label, fn in CHECKS:
        try:
            fn()
            print(f"PASS  {label}")
        except Exception as exc:
            failures += 1
            print(f"FAIL  {label}\n      {exc}")
    print(f"\n{len(CHECKS) - failures}/{len(CHECKS)} checks passed.")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
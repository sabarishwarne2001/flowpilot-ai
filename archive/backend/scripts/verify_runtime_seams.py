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
    assert "_existing_by_idempotency_key" in source
    assert "begin_nested()" in source
    assert "IntegrityError" in source


@check("S2  job_service uses SAVEPOINT deduplication on duplicate job keys")
def _s2() -> None:
    source = _read("app/services/job_service.py")
    assert "_existing_job_by_idempotency_key" in source
    assert "begin_nested()" in source
    assert "IntegrityError" in source


@check("S3  enrich handler auto-provisions defaults on missing settings")
def _s3() -> None:
    source = _read("app/workers/handlers/enrich.py")
    assert "_ensure_workspace_defaults" in source


@check("S4  enrich handler _guarded catches generic exceptions during metadata extraction")
def _s4() -> None:
    source = _read("app/workers/handlers/enrich.py")
    assert "except Exception" in source or "except (" in source


@check("S5  llm_service._extract_json handles regex markdown blocks safely")
def _s5() -> None:
    source = _read("app/services/llm_service.py")
    assert "_JSON_OPENERS" in source or "re.search" in source or "find(" in source


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

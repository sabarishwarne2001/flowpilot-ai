#!/usr/bin/env python
"""ARCH-0V Tranche 1 — run verification gates in phase order with UTF-8 encoding."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

BACKEND_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = BACKEND_ROOT / "scripts"

LIVE_DB_GATES: frozenset[str] = frozenset(
    {
        "verify_arch20.py",
    }
)

STATIC_ONLY_FLAG: dict[str, str] = {
    "verify_arch19.py": "--static-only",
    "verify_arch20.py": "--static-only",
    "verify_arch22.py": "--verbose",
}

SPECIAL_ORDER: dict[str, tuple[int, int]] = {
    "verify_migration_history.py": (0, 0),
    "verify_models_step2.py": (2, 50),
    "verify_step4.py": (4, 50),
    "verify_platform_email.py": (6, 50),
    "verify_scope_vocabulary.py": (8, 50),
    "verify_sec1.py": (15, 50),
    "verify_arch16_full_compatibility.py": (16, 99),
    "verify_arch0g.py": (16, 100),
    "verify_arch0v.py": (99, 0),
}

_PHASE_RE = re.compile(r"^verify_arch(\d+)", re.IGNORECASE)
_STEP_RE = re.compile(r"step(\d+)", re.IGNORECASE)


class UnclassifiableGate(Exception):
    """A discovered gate that cannot be placed in the run order."""


@dataclass
class GateResult:
    name: str
    order: tuple[int, int]
    status: str
    duration_s: float
    returncode: Optional[int] = None
    detail: str = ""
    tail: list[str] = field(default_factory=list)


def _sort_key(filename: str) -> tuple[int, int]:
    if filename in SPECIAL_ORDER:
        return SPECIAL_ORDER[filename]

    phase_match = _PHASE_RE.match(filename)
    if phase_match is None:
        raise UnclassifiableGate(
            f"{filename} does not match 'verify_arch<NN>...' and has no entry in SPECIAL_ORDER."
        )

    phase = int(phase_match.group(1))
    step_match = _STEP_RE.search(filename)
    step = int(step_match.group(1)) if step_match else 90
    return (phase, step)


def discover() -> list[tuple[str, tuple[int, int]]]:
    found = sorted(p.name for p in SCRIPTS_DIR.glob("verify_*.py"))
    if not found:
        raise SystemExit(f"No verify_*.py gates found under {SCRIPTS_DIR}.")

    ordered: list[tuple[str, tuple[int, int]]] = []
    unclassifiable: list[str] = []
    for name in found:
        try:
            ordered.append((name, _sort_key(name)))
        except UnclassifiableGate as exc:
            unclassifiable.append(str(exc))

    if unclassifiable:
        print("\nUNCLASSIFIABLE GATES:\n")
        for message in unclassifiable:
            print(f"  * {message}\n")
        raise SystemExit(2)

    ordered.sort(key=lambda item: (item[1], item[0]))
    return ordered


def _run_one(name: str, *, static_only: bool, timeout: int) -> GateResult:
    order = _sort_key(name)
    path = SCRIPTS_DIR / name

    if static_only and name in LIVE_DB_GATES and name not in STATIC_ONLY_FLAG:
        return GateResult(
            name=name,
            order=order,
            status="SKIP",
            duration_s=0.0,
            detail="needs live database",
        )

    argv = [sys.executable, str(path)]
    if static_only and name in STATIC_ONLY_FLAG:
        argv.append(STATIC_ONLY_FLAG[name])

    started = time.perf_counter()
    try:
        completed = subprocess.run(
            argv,
            cwd=str(BACKEND_ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            env={**os.environ, "PYTHONPATH": str(BACKEND_ROOT)},
        )
    except subprocess.TimeoutExpired:
        return GateResult(
            name=name,
            order=order,
            status="ERROR",
            duration_s=time.perf_counter() - started,
            detail=f"timed out after {timeout}s",
        )

    duration = time.perf_counter() - started
    output = (completed.stdout or "") + (completed.stderr or "")
    tail = [line for line in output.strip().splitlines() if line.strip()][-12:]

    return GateResult(
        name=name,
        order=order,
        status="PASS" if completed.returncode == 0 else "FAIL",
        duration_s=duration,
        returncode=completed.returncode,
        tail=tail,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Run verification gates.")
    parser.add_argument("--static-only", action="store_true")
    parser.add_argument("--only", nargs="*", default=None)
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--json", default=None)
    parser.add_argument("--timeout", type=int, default=600)
    parser.add_argument("--fail-fast", action="store_true")
    args = parser.parse_args()

    ordered = discover()

    if args.only:
        needles = [n.lower() for n in args.only]
        ordered = [
            item for item in ordered
            if any(needle in item[0].lower() for needle in needles)
        ]

    if args.list:
        print(f"{len(ordered)} gate(s), in run order:\n")
        for name, order in ordered:
            marker = "  [live-db]" if name in LIVE_DB_GATES else ""
            print(f"  {order[0]:>3}.{order[1]:<3}  {name}{marker}")
        return 0

    print("=" * 78)
    print(f"FlowPilot AI — verification gate suite ({len(ordered)} gates)")
    print("=" * 78)

    results: list[GateResult] = []
    for index, (name, _order) in enumerate(ordered, start=1):
        print(f"[{index:>2}/{len(ordered)}] {name:<45}", end="", flush=True)
        result = _run_one(name, static_only=args.static_only, timeout=args.timeout)
        results.append(result)

        if result.status == "PASS":
            print(f"PASS  ({result.duration_s:5.2f}s)")
        elif result.status == "SKIP":
            print(f"SKIP  ({result.detail})")
        else:
            print(f"{result.status}  ({result.duration_s:5.2f}s)")

    passed = sum(1 for r in results if r.status == "PASS")
    failed = sum(1 for r in results if r.status in ("FAIL", "ERROR"))
    skipped = sum(1 for r in results if r.status == "SKIP")
    print("=" * 78)
    print(f"{passed} passed / {failed} failed / {skipped} skipped")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

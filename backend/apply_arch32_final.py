#!/usr/bin/env python3
"""ARCH-32 Steps 2-3 — anchored, idempotent patches for MODIFIED files.

    python apply_arch32_final.py --check
    python apply_arch32_final.py
    python apply_arch32_final.py          # again: "already applied", no-op

Run AFTER `apply_arch32.py`. This one wires the handlers, the worker profile,
the API router and the console routes — all of which reference modules that
Step 1 did not create.

THE THREE-PLACE RULE IS WHY THIS IS ONE SCRIPT AND NOT THREE
============================================================

`app/workers/handlers/__init__.py`, `app/workers/profiles.py` and the job-type
vocabulary have to change together or the fleet does not boot.
`assert_imports_match_profile()` runs `uncovered_job_types()` at EVERY
worker's startup and raises `ProfileError` on a handler no profile claims —
so a run that patched the handler map and then failed on the profile anchor
would leave a tree where no worker starts.

Every edit for those three files is therefore in ONE `FilePatch` list that is
validated in full before a single byte is written: `apply_patch` computes
every replacement against an in-memory copy and only then touches the disk,
and the runner aborts the whole run on the first anchor failure.

`_merge_by_path`
================

Frontend route tables and import blocks are edited by several phases, so a
literal anchor on "the line above" rots. `_merge_by_path` takes a file, a
path-like key (`ROUTE_PATTERNS.workspaceRedaction`) and the text to insert,
finds the enclosing object literal, and inserts only if the key is absent.
Re-running is a no-op because the key is already there — the same idempotence
the sentinel gives, keyed on meaning rather than on a comment.

SENTINELS
=========
Every sentinel is a substring of the text its own patch writes. `verify_arch32`
asserts that mechanically for both patch scripts.
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

ROOT = Path(__file__).resolve().parent.parent
BACKEND = ROOT / "backend"
FRONTEND = ROOT / "frontend"

SENTINEL_HANDLERS = "ARCH32_JOB_TYPES"
SENTINEL_PROFILES = "ARCH-32 redaction. Rendering at 300 DPI"
SENTINEL_ROUTER = "# ARCH-32 zero-leakage redaction"
SENTINEL_ROUTES = "workspaceRedaction"
SENTINEL_APP = "RedactionStudio"


@dataclass
class Edit:
    anchor: str
    replacement: str
    occurrences: int = 1
    description: str = ""


@dataclass
class FilePatch:
    root: Path
    relpath: str
    sentinel: str
    edits: list[Edit] = field(default_factory=list)
    precondition: Optional[Callable[[str], Optional[str]]] = None


def _read(path: Path) -> tuple[str, str, bool]:
    raw = path.read_bytes()
    had_bom = raw.startswith(b"\xef\xbb\xbf")
    if had_bom:
        raw = raw[3:]
    text = raw.decode("utf-8")
    newline = "\r\n" if "\r\n" in text else "\n"
    return text.replace("\r\n", "\n"), newline, had_bom


def _write(path: Path, text: str, newline: str, had_bom: bool) -> None:
    body = text.replace("\n", newline) if newline != "\n" else text
    data = body.encode("utf-8")
    if had_bom:
        data = b"\xef\xbb\xbf" + data
    path.write_bytes(data)


def _merge_by_path(text: str, *, key: str, entry: str, after: str) -> str:
    """Insert `entry` into an object literal, keyed on `key` being absent.

    `after` is an existing sibling line used only to locate the insertion
    point. Unlike a literal anchor it does not have to be immediately
    adjacent to anything, and unlike a line number it does not rot when a
    neighbouring phase adds a key above it.
    """
    if re.search(rf"\b{re.escape(key)}\b", text):
        return text
    index = text.find(after)
    if index < 0:
        raise PatchError(f"cannot locate the sibling entry {after!r} to merge beside")
    end = text.find("\n", index)
    if end < 0:
        raise PatchError(f"sibling entry {after!r} is not on its own line")
    return text[: end + 1] + entry + text[end + 1 :]


# ---------------------------------------------------------------------------
# 1. Handler map
# ---------------------------------------------------------------------------

HANDLERS_FN_ANCHOR = """def _analytics_export_sync(payload: dict[str, Any]) -> dict[str, Any]:"""

HANDLERS_FN_REPLACEMENT = '''def _redaction_detect(payload: dict[str, Any]) -> dict[str, Any]:
    from app.workers.handlers.redaction import handle_redaction_detect
    return handle_redaction_detect(payload)


def _redaction_apply(payload: dict[str, Any]) -> dict[str, Any]:
    from app.workers.handlers.redaction import handle_redaction_apply
    return handle_redaction_apply(payload)


def _analytics_export_sync(payload: dict[str, Any]) -> dict[str, Any]:'''

HANDLERS_CONST_ANCHOR = (
    'ARCH31_JOB_TYPES: frozenset[str] = frozenset({"procurement.score"})'
)

HANDLERS_CONST_REPLACEMENT = (
    'ARCH31_JOB_TYPES: frozenset[str] = frozenset({"procurement.score"})\n'
    "ARCH32_JOB_TYPES: frozenset[str] = frozenset(\n"
    '    {"redaction.detect", "redaction.apply"}\n'
    ")"
)

HANDLERS_UNION_ANCHOR = "    | ARCH31_JOB_TYPES\n)"

HANDLERS_UNION_REPLACEMENT = "    | ARCH31_JOB_TYPES\n    | ARCH32_JOB_TYPES\n)"

HANDLERS_DICT_ANCHOR = '    "procurement.score": _procurement_score,\n}'

HANDLERS_DICT_REPLACEMENT = (
    '    "procurement.score": _procurement_score,\n'
    "    # ARCH-32. Also claimed by the OCR profile in\n"
    "    # app/workers/profiles.py. NOT in DEFAULT_SCHEDULE: neither job is\n"
    "    # recurring, and re-running an apply on a schedule would re-render\n"
    "    # and re-upload a document somebody may have cancelled. A handler\n"
    "    # here with no profile there is a job that enqueues cleanly and\n"
    "    # never runs -- and assert_imports_match_profile() raises\n"
    "    # ProfileError at every worker's startup on a handler no profile\n"
    "    # claims, so registering these without the profile entry stops the\n"
    "    # entire fleet booting.\n"
    '    "redaction.detect": _redaction_detect,\n'
    '    "redaction.apply": _redaction_apply,\n'
    "}"
)

HANDLERS_EXPORT_ANCHOR = '    "ARCH31_JOB_TYPES",'

HANDLERS_EXPORT_REPLACEMENT = '    "ARCH31_JOB_TYPES",\n    "ARCH32_JOB_TYPES",'


# ---------------------------------------------------------------------------
# 2. Worker profile — the OCR image, not LIGHT
# ---------------------------------------------------------------------------

PROFILES_ANCHOR = """OCR = WorkerProfile(
    name="ocr",
    job_types=frozenset({"document.extract"}),
    allow_heavy=frozenset({"paddleocr", "paddle"}),
    description="Heavy image. Document text extraction only.",
)"""

PROFILES_REPLACEMENT = '''OCR = WorkerProfile(
    name="ocr",
    job_types=frozenset(
        {
            "document.extract",
            # ARCH-32 redaction. Rendering at 300 DPI through PDFium and
            # holding a full-page uint8 array while it burns is the same
            # class of work `document.extract` does, and it is what the
            # heavy image exists to carry. On LIGHT, a forty-page scan
            # would exhaust a worker that also serves notification
            # delivery and billing reconciliation.
            #
            # `redaction.detect` is cheaper -- it reads text that is
            # already extracted -- but it opens the PDF for page
            # dimensions and 3.3 reserves re-running PaddleOCR geometry
            # for pages the text layer did not cover. Splitting the two
            # across profiles would mean a detect that cannot grow into
            # that without migrating the queue.
            #
            # As with every entry in LIGHT above, this is not optional
            # bookkeeping: assert_imports_match_profile() raises
            # ProfileError at EVERY worker's startup on a handler no
            # profile claims. Registering these in handlers/__init__.py
            # without adding them here stops the entire fleet booting.
            "redaction.detect",
            "redaction.apply",
        }
    ),
    allow_heavy=frozenset({"paddleocr", "paddle"}),
    description=(
        "Heavy image. Document text extraction and ARCH-32 redaction "
        "rendering."
    ),
)'''


# ---------------------------------------------------------------------------
# 3. API router
# ---------------------------------------------------------------------------

ROUTER_IMPORT_ANCHOR = """    procurement,"""
ROUTER_IMPORT_REPLACEMENT = """    procurement,
    redactions,"""

ROUTER_INCLUDE_ANCHOR = (
    """api_router.include_router(procurement.router)  # ARCH-31 three-way matching"""
)
ROUTER_INCLUDE_REPLACEMENT = """api_router.include_router(procurement.router)  # ARCH-31 three-way matching
# ARCH-32 zero-leakage redaction. Every route in it is capability-gated,
# including the reads: the detection output is a map of where every
# identifier in a tenant's corpus sits, which IS the product.
api_router.include_router(redactions.router)"""


PATCHES: list[FilePatch] = [
    FilePatch(
        root=BACKEND,
        relpath="app/workers/handlers/__init__.py",
        sentinel=SENTINEL_HANDLERS,
        edits=[
            Edit(
                anchor=HANDLERS_FN_ANCHOR,
                replacement=HANDLERS_FN_REPLACEMENT,
                description="dispatch functions for the two redaction jobs",
            ),
            Edit(
                anchor=HANDLERS_CONST_ANCHOR,
                replacement=HANDLERS_CONST_REPLACEMENT,
                description="declare ARCH32_JOB_TYPES",
            ),
            Edit(
                anchor=HANDLERS_UNION_ANCHOR,
                replacement=HANDLERS_UNION_REPLACEMENT,
                description="add ARCH32_JOB_TYPES to ALL_PHASE_JOB_TYPES",
            ),
            Edit(
                anchor=HANDLERS_DICT_ANCHOR,
                replacement=HANDLERS_DICT_REPLACEMENT,
                description="register both handlers in _HANDLERS",
            ),
            Edit(
                anchor=HANDLERS_EXPORT_ANCHOR,
                replacement=HANDLERS_EXPORT_REPLACEMENT,
                description="export ARCH32_JOB_TYPES",
            ),
        ],
    ),
    FilePatch(
        root=BACKEND,
        relpath="app/workers/profiles.py",
        sentinel=SENTINEL_PROFILES,
        edits=[
            Edit(
                anchor=PROFILES_ANCHOR,
                replacement=PROFILES_REPLACEMENT,
                description="claim redaction.detect and redaction.apply on OCR",
            )
        ],
    ),
    FilePatch(
        root=BACKEND,
        relpath="app/api/v1/router.py",
        sentinel=SENTINEL_ROUTER,
        edits=[
            Edit(
                anchor=ROUTER_IMPORT_ANCHOR,
                replacement=ROUTER_IMPORT_REPLACEMENT,
                description="import the redactions router",
            ),
            Edit(
                anchor=ROUTER_INCLUDE_ANCHOR,
                replacement=ROUTER_INCLUDE_REPLACEMENT,
                description="mount the redactions router",
            ),
        ],
    ),
]


class PatchError(RuntimeError):
    pass


def _patch_routes(check_only: bool) -> str:
    """`_merge_by_path` on the console's route table."""
    path = FRONTEND / "src/routes/tenantPaths.ts"
    if not path.exists():
        raise PatchError(f"{path} does not exist")
    text, newline, bom = _read(path)
    if SENTINEL_ROUTES in text:
        return "OK    frontend/src/routes/tenantPaths.ts: already applied"
    updated = _merge_by_path(
        text,
        key="workspaceRedaction",
        entry='  workspaceRedaction: "redactions/:jobId",\n',
        after='workspaceProcurementPolicies: "procurement/policies",',
    )
    if check_only:
        return "WOULD frontend/src/routes/tenantPaths.ts: 1 entry"
    _write(path, updated, newline, bom)
    return "WROTE frontend/src/routes/tenantPaths.ts: 1 entry"


def _patch_app(check_only: bool) -> str:
    path = FRONTEND / "src/App.tsx"
    if not path.exists():
        raise PatchError(f"{path} does not exist")
    text, newline, bom = _read(path)
    if SENTINEL_APP in text:
        return "OK    frontend/src/App.tsx: already applied"

    lazy_anchor = 'const ProcurementCaseQueue = lazy('
    if lazy_anchor not in text:
        raise PatchError("App.tsx: cannot find the procurement lazy import block")
    updated = text.replace(
        lazy_anchor,
        'const RedactionStudio = lazy(\n  () => import("@/pages/redaction/RedactionStudio"),\n);\n'
        + lazy_anchor,
        1,
    )

    route_anchor = "                      path={ROUTE_PATTERNS.workspaceProcurement}"
    if route_anchor not in updated:
        raise PatchError("App.tsx: cannot find the procurement route block")
    updated = updated.replace(
        route_anchor,
        "                      path={ROUTE_PATTERNS.workspaceRedaction}\n"
        "                      element={<RedactionStudio />}\n"
        "                    />\n"
        "                    <Route\n" + route_anchor,
        1,
    )
    if check_only:
        return "WOULD frontend/src/App.tsx: 2 edit(s)"
    _write(path, updated, newline, bom)
    return "WROTE frontend/src/App.tsx: 2 edit(s)"


def apply_patch(patch: FilePatch, *, check_only: bool) -> str:
    path = patch.root / patch.relpath
    if not path.exists():
        raise PatchError(f"{patch.relpath}: file does not exist")
    text, newline, had_bom = _read(path)

    if patch.precondition is not None:
        reason = patch.precondition(text)
        if reason:
            return f"SKIP  {patch.relpath}: {reason}"

    if patch.sentinel in text:
        return f"OK    {patch.relpath}: already applied"

    updated = text
    for edit in patch.edits:
        found = updated.count(edit.anchor)
        if found != edit.occurrences:
            raise PatchError(
                f"{patch.relpath}: anchor for {edit.description!r} occurs "
                f"{found} time(s), expected {edit.occurrences}. Nothing "
                "has been written."
            )
        updated = updated.replace(edit.anchor, edit.replacement, edit.occurrences)

    if patch.sentinel not in updated:
        raise PatchError(
            f"{patch.relpath}: sentinel {patch.sentinel!r} is absent from the "
            "patched text. A sentinel must be a substring of what its own "
            "patch writes."
        )

    if check_only:
        return f"WOULD {patch.relpath}: {len(patch.edits)} edit(s)"
    _write(path, updated, newline, had_bom)
    return f"WROTE {patch.relpath}: {len(patch.edits)} edit(s)"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()

    print("ARCH-32 Steps 2-3 — wiring patches")
    print(f"repo: {ROOT}")
    print()

    results: list[str] = []
    try:
        for patch in PATCHES:
            results.append(apply_patch(patch, check_only=args.check))
        results.append(_patch_routes(args.check))
        results.append(_patch_app(args.check))
    except PatchError as exc:
        for line in results:
            print(f"  {line}")
        print(f"\n  FAIL  {exc}")
        return 1

    for line in results:
        print(f"  {line}")
    changed = sum(1 for line in results if line.startswith(("WOULD", "WROTE")))
    print()
    print(
        f"{changed} file(s) would change. Nothing was written."
        if args.check
        else f"{changed} file(s) changed."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
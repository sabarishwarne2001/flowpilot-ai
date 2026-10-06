#!/usr/bin/env python3
"""ARCH-31 Steps 1-2 — anchored, idempotent patches for MODIFIED files.

    python apply_arch31.py --check     # report, change nothing
    python apply_arch31.py             # apply
    python apply_arch31.py             # again: reports "already applied", no-op

New files are delivered whole and are not this script's business. This script
touches only files that already existed and must keep everything else in them.

WHY ANCHORS AND SENTINELS RATHER THAN LINE NUMBERS
==================================================

Line numbers rot the moment anything above them changes, and a patch that
applies at the wrong offset does not fail — it corrupts. Each edit here names
the exact text it replaces, asserts how many times that text occurs, and
writes a sentinel comment. Re-running finds the sentinel and skips.

An anchor that matches zero times is a HARD FAILURE, not a warning. It means
the file is not in the state this patch was written against, and continuing
would apply a subset of the changes and leave the tree in a state no gate
describes.

An anchor that matches more than its declared count is equally fatal: the
patch author believed the text was unique and it is not, so which occurrence
gets edited is luck.

BOM AND LINE ENDINGS
====================

`app/api/v1/router.py` begins with a UTF-8 BOM. `git config core.autocrlf` on
Windows leaves CRLF in the working tree. Reading with `utf-8-sig` and writing
back without preserving both would produce a whole-file diff on a one-line
change, and on `router.py` it would strip a BOM the file has carried since
ARCH-01. Every write here restores exactly what it found.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

BACKEND = Path(__file__).resolve().parent

SENTINEL_QUANTITY_HINT = "ARCH-31 Step 2. `currency_hint` was hard-coded"
SENTINEL_MODELS_EXPORT = "# ARCH-31 — procurement three-way matching."


@dataclass
class Edit:
    """One anchored replacement within one file."""

    anchor: str
    replacement: str
    #: How many times `anchor` must occur. Anything else is a hard failure.
    occurrences: int = 1
    description: str = ""


@dataclass
class FilePatch:
    relpath: str
    sentinel: str
    edits: list[Edit] = field(default_factory=list)
    #: Optional guard: a reason to skip this file entirely (e.g. the target
    #: symbol already exists for a different reason).
    precondition: Optional[Callable[[str], Optional[str]]] = None


def _read(path: Path) -> tuple[str, str, bool]:
    """Return (text, newline, had_bom) with the file's own conventions intact."""
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


# ---------------------------------------------------------------------------
# The patches
# ---------------------------------------------------------------------------

QUANTITY_ANCHOR = '''def _decimal_from(raw: str) -> Decimal:
    """Quantities use the same grouping rules as money, minus the currency.

    Re-uses `money_micros` deliberately rather than a second parser: a
    quantity written `1,234.500` must not be read one way on a PO and another
    on the invoice because two different functions read it.
    """
    parsed = money_micros(raw, currency_hint="USD")
    return Decimal(parsed.micros) / MICROS'''

QUANTITY_REPLACEMENT = '''def _decimal_from(raw: str, *, currency_hint: str = "USD") -> Decimal:
    """Quantities use the same grouping rules as money, minus the currency.

    Re-uses `money_micros` deliberately rather than a second parser: a
    quantity written `1,234.500` must not be read one way on a PO and another
    on the invoice because two different functions read it.

    ARCH-31 Step 2. `currency_hint` was hard-coded to "USD" here, which meant
    a quantity carried the Western grouping convention no matter where the
    document came from. On a German purchase order `1.000` units then read as
    1 rather than 1000 — a 1000x error on the exact axis that decides
    QUANTITY_VARIANCE, silent, and pointing at the supplier.

    The default remains "USD" so that every existing caller and every ARCH-31
    Step 0 gate keeps its current answer. The matcher passes the currency it
    resolved for the document, so a EUR line reads `1.000` as a thousand and
    an INR line reads it as one.
    """
    parsed = money_micros(raw, currency_hint=currency_hint)
    return Decimal(parsed.micros) / MICROS'''

MODELS_IMPORT_ANCHOR = """    # ARCH-27 — partner marketplace, reseller tenancy and revenue share.
    \"MarketplaceInstallation\","""

MODELS_IMPORT_REPLACEMENT = """    # ARCH-31 — procurement three-way matching.
    #
    # DocumentRole maps the table arch31_step0_document_roles created; Step 0
    # shipped it without a mapped class because nothing read it yet.
    # candidates.py is the first reader.
    \"DocumentRole\",
    \"ProcurementCase\",
    \"ProcurementCaseLine\",
    \"ProcurementTolerancePolicy\",
    # ARCH-27 — partner marketplace, reseller tenancy and revenue share.
    \"MarketplaceInstallation\","""


PATCHES: list[FilePatch] = [
    FilePatch(
        relpath="app/core/normalize.py",
        sentinel=SENTINEL_QUANTITY_HINT,
        edits=[
            Edit(
                anchor=QUANTITY_ANCHOR,
                replacement=QUANTITY_REPLACEMENT,
                description="thread a currency hint through the quantity parser",
            ),
            Edit(
                anchor="def quantity(raw: Optional[str]) -> Quantity:",
                replacement=(
                    "def quantity(\n"
                    "    raw: Optional[str], *, currency_hint: Optional[str] = None\n"
                    ") -> Quantity:"
                ),
                description="accept the hint on the public function",
            ),
            Edit(
                anchor=(
                    '    text = re.sub(r"\\s+", " ", _ascii_fold(text))\n\n'
                    "    match = _PACK_OF.match(text)"
                ),
                replacement=(
                    '    text = re.sub(r"\\s+", " ", _ascii_fold(text))\n'
                    '    hint = (currency_hint or "USD").strip().upper() or "USD"\n\n'
                    "    match = _PACK_OF.match(text)"
                ),
                description="resolve the hint once, before either branch reads it",
            ),
            Edit(
                anchor=(
                    "        return Quantity(\n"
                    "            value=_decimal_from(outer),\n"
                    "            unit=unit,\n"
                    "            pack_size=_decimal_from(inner),\n"
                    "        )"
                ),
                replacement=(
                    "        return Quantity(\n"
                    "            value=_decimal_from(outer, currency_hint=hint),\n"
                    "            unit=unit,\n"
                    "            pack_size=_decimal_from(inner, currency_hint=hint),\n"
                    "        )"
                ),
                description="pack-of branch honours the hint on both numbers",
            ),
            Edit(
                anchor="    return Quantity(value=_decimal_from(number), unit=unit)",
                replacement=(
                    "    return Quantity(\n"
                    "        value=_decimal_from(number, currency_hint=hint), unit=unit\n"
                    "    )"
                ),
                description="plain branch honours the hint",
            ),
        ],
    ),
    FilePatch(
        relpath="app/models/__init__.py",
        sentinel=SENTINEL_MODELS_EXPORT,
        edits=[
            # The import must land as well as the __all__ entry. An __all__
            # name with no import is not merely a broken star-import: the
            # mapped class never reaches the declarative registry, so
            # `alembic revision --autogenerate` sees three tables in the
            # database that no model describes and proposes to DROP them.
            Edit(
                anchor="from app.models.partner import (  # noqa: F401",
                replacement=(
                    "from app.models.document_role import DocumentRole  # noqa: F401\n"
                    "from app.models.procurement import (  # noqa: F401\n"
                    "    ProcurementCase,\n"
                    "    ProcurementCaseLine,\n"
                    "    ProcurementTolerancePolicy,\n"
                    ")\n"
                    "from app.models.partner import (  # noqa: F401"
                ),
                description="import the four new mapped classes",
            ),
            Edit(
                anchor=MODELS_IMPORT_ANCHOR,
                replacement=MODELS_IMPORT_REPLACEMENT,
                description="export the four new mapped classes",
            ),
        ],
    ),
]


def _merge_by_path(patches: list[FilePatch]) -> dict[str, FilePatch]:
    """Collapse patches that touch the same file into one write.

    Two FilePatch entries for one path would each read the file, apply their
    own edits and write — and the second read would see the first's result
    only by luck of ordering. One entry per path, asserted here rather than
    assumed.
    """
    merged: dict[str, FilePatch] = {}
    for patch in patches:
        existing = merged.get(patch.relpath)
        if existing is None:
            merged[patch.relpath] = patch
            continue
        if existing.sentinel != patch.sentinel:
            raise SystemExit(
                f"two patches for {patch.relpath} declare different "
                f"sentinels; idempotency would be undecidable"
            )
        existing.edits.extend(patch.edits)
    return merged


def apply_patch(patch: FilePatch, *, check_only: bool) -> str:
    path = BACKEND / patch.relpath
    if not path.exists():
        raise SystemExit(f"FATAL: {patch.relpath} does not exist")

    text, newline, had_bom = _read(path)

    if patch.sentinel in text:
        return "already applied"

    if patch.precondition is not None:
        reason = patch.precondition(text)
        if reason:
            return f"skipped: {reason}"

    updated = text
    for edit in patch.edits:
        found = updated.count(edit.anchor)
        if found != edit.occurrences:
            raise SystemExit(
                f"FATAL: {patch.relpath}: anchor for {edit.description!r} "
                f"occurs {found} time(s), expected {edit.occurrences}.\n"
                f"  The file is not in the state this patch was written "
                f"against. Applying a subset would leave the tree in a state "
                f"no gate describes.\n"
                f"  anchor: {edit.anchor[:90]!r}..."
            )
        updated = updated.replace(edit.anchor, edit.replacement, edit.occurrences)

    if updated == text:
        return "no change"

    if check_only:
        return f"would apply {len(patch.edits)} edit(s)"

    _write(path, updated, newline, had_bom)
    return f"applied {len(patch.edits)} edit(s)"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check", action="store_true", help="report what would change, write nothing"
    )
    args = parser.parse_args()

    print("ARCH-31 Steps 1-2 — patching modified files")
    print(f"  backend: {BACKEND}")
    print(f"  mode:    {'CHECK (no writes)' if args.check else 'APPLY'}\n")

    merged = _merge_by_path(PATCHES)
    for relpath, patch in merged.items():
        status = apply_patch(patch, check_only=args.check)
        print(f"  [{status:>22}] {relpath}")

    print(
        "\nRe-running this script is safe: each file carries a sentinel and "
        "is skipped once present."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
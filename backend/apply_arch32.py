#!/usr/bin/env python3
"""ARCH-32 Step 1 — anchored, idempotent patches for MODIFIED files.

    python apply_arch32.py --check     # report, change nothing
    python apply_arch32.py             # apply
    python apply_arch32.py             # again: reports "already applied", no-op

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

EVERY SENTINEL IS A SUBSTRING OF THE TEXT ITS OWN PATCH WRITES
==============================================================

ARCH-31 established this and it is not decoration. A sentinel that is not
present in the replacement text makes the idempotence check a lie: the second
run does not find it, re-applies the edit, and the anchor is gone — so the
run fails with "anchor not found" on a tree that is actually correct.
`verify_arch32.py` asserts the property mechanically for every patch in this
file rather than trusting the author to have held it in their head.

BOM AND LINE ENDINGS
====================

`app/api/v1/router.py` begins with a UTF-8 BOM, and `git config core.autocrlf`
on Windows leaves CRLF in the working tree. Reading with `utf-8-sig` and
writing back without preserving both would produce a whole-file diff on a
one-line change. Every write here restores exactly what it found.

WHAT THIS STEP DOES NOT TOUCH
=============================

`app/workers/handlers/__init__.py`, `app/workers/profiles.py` and
`app/api/v1/router.py` are Step 2's business, because the handlers and the
router module they would register do not exist yet. Registering a handler in
one of the three places and not the others is the exact defect ARCH-16 shipped
and ARCH-27's comments memorialise: `assert_imports_match_profile()` raises
`ProfileError` at EVERY worker's startup on a handler no profile claims, so a
half-registration here would stop the fleet booting rather than degrade.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

BACKEND = Path(__file__).resolve().parent

SENTINEL_MODELS_IMPORT = "# ARCH-32 — zero-leakage geometric PII redaction."
SENTINEL_ENTITLEMENTS = "ARCH32-S1:capability-redaction-key"
SENTINEL_USAGE_EVENT = "ARCH-32 §3.7 — one PAGE of redacted OUTPUT."
SENTINEL_DISPLAY_NAME = "ARCH32-S1:capability-redaction-display"
SENTINEL_REQUIREMENTS = "pikepdf==10.5.1"


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
# 1. app/models/__init__.py — map the two new tables
# ---------------------------------------------------------------------------

MODELS_IMPORT_ANCHOR = """from app.models.procurement import (  # noqa: F401
    ProcurementCase,
    ProcurementCaseLine,
    ProcurementTolerancePolicy,
)"""

MODELS_IMPORT_REPLACEMENT = """from app.models.procurement import (  # noqa: F401
    ProcurementCase,
    ProcurementCaseLine,
    ProcurementTolerancePolicy,
)
# ARCH-32 — zero-leakage geometric PII redaction.
#
# Imported here for the same reason every other mapped class is: the
# declarative registry has to know about a table before anything can query it,
# and a model that is only imported by the module that uses it produces a
# `NoSuchTableError` at the first cross-module relationship rather than at
# import.
from app.models.redaction import (  # noqa: F401
    RedactionJob,
    RedactionRegion,
)"""

MODELS_ALL_ANCHOR = """    "ProcurementCase",
    "ProcurementCaseLine",
    "ProcurementTolerancePolicy",
"""

MODELS_ALL_REPLACEMENT = """    "ProcurementCase",
    "ProcurementCaseLine",
    "ProcurementTolerancePolicy",
    # ARCH-32 — zero-leakage geometric PII redaction.
    "RedactionJob",
    "RedactionRegion",
"""

# ---------------------------------------------------------------------------
# 2. app/core/entitlements.py — register capability.redaction
# ---------------------------------------------------------------------------

ENTITLEMENTS_EXPORT_ANCHOR = """    # ARCH31-S0:capability-reconciliation-export
    "CAPABILITY_KEYS",
    "RECONCILIATION_CAPABILITY","""

ENTITLEMENTS_EXPORT_REPLACEMENT = """    # ARCH31-S0:capability-reconciliation-export
    "CAPABILITY_KEYS",
    "RECONCILIATION_CAPABILITY",
    # ARCH32-S1:capability-redaction-key
    "REDACTION_CAPABILITY","""

ENTITLEMENTS_KEY_ANCHOR = """#: Every capability key. Disjoint from ADDON_KEYS by construction;
#: verify_arch31_step0 asserts the two sets never intersect.
CAPABILITY_KEYS: tuple[str, ...] = (RECONCILIATION_CAPABILITY,)"""

ENTITLEMENTS_KEY_REPLACEMENT = '''#: ARCH32-S1:capability-redaction-key. Zero-leakage geometric PII
#: redaction. A CAPABILITY, not an ADDON, and the distinction is the
#: same one ARCH-31 recorded above: add-ons are separately purchasable
#: line items with a price, a halt effect and a grace ladder, and
#: `ADDON_KEYS` is asserted equal to `entitlement_service`'s catalog at
#: import. §8 of the roadmap bundles redaction as "Enterprise; add-on
#: for Business", and the add-on half of that sentence is a PACKAGING
#: decision made in a published tier version — not a second key here.
#: Putting it in ADDON_KEYS would fail the catalog assertion at boot.
REDACTION_CAPABILITY: str = "capability.redaction"

#: Every capability key. Disjoint from ADDON_KEYS by construction;
#: verify_arch31_step0 asserts the two sets never intersect.
CAPABILITY_KEYS: tuple[str, ...] = (
    RECONCILIATION_CAPABILITY,
    REDACTION_CAPABILITY,
)'''

ENTITLEMENTS_ENTRY_ANCHOR = '''    # ARCH31-S0:capability-reconciliation-entitlement
    Entitlement(
        name=RECONCILIATION_CAPABILITY,
        description=(
            "Procurement three-way matching: reconcile purchase orders, "
            "goods receipts and supplier invoices line by line, with "
            "tolerance policies and evidence. Bundled into a tier, not "
            "purchasable on its own."
        ),
    ),
)'''

ENTITLEMENTS_ENTRY_REPLACEMENT = '''    # ARCH31-S0:capability-reconciliation-entitlement
    Entitlement(
        name=RECONCILIATION_CAPABILITY,
        description=(
            "Procurement three-way matching: reconcile purchase orders, "
            "goods receipts and supplier invoices line by line, with "
            "tolerance policies and evidence. Bundled into a tier, not "
            "purchasable on its own."
        ),
    ),
    # ARCH32-S1:capability-redaction-key
    Entitlement(
        name=REDACTION_CAPABILITY,
        description=(
            "Zero-leakage redaction: rebuild a document from redacted "
            "pixels so removed content does not exist in the output, with "
            "a leak-checked sanitized PDF and a manifest that carries no "
            "redacted text. Bundled into a tier."
        ),
    ),
)'''

# ---------------------------------------------------------------------------
# 3. app/core/usage_events.py — register redaction.page
# ---------------------------------------------------------------------------

USAGE_EVENT_ANCHOR = '''    UsageEventType(
        name="procurement.case",
        unit=UsageUnit.REQUEST,
        emission=EmissionKind.OCCURRENCE,
        billable=True,
        default_provider="internal",
        description="One procurement case scored against a tolerance policy.",
    ),
)'''

USAGE_EVENT_REPLACEMENT = '''    UsageEventType(
        name="procurement.case",
        unit=UsageUnit.REQUEST,
        emission=EmissionKind.OCCURRENCE,
        billable=True,
        default_provider="internal",
        description="One procurement case scored against a tolerance policy.",
    ),
    # ARCH-32 §3.7 — one PAGE of redacted OUTPUT.
    #
    # PAGE rather than REQUEST, unlike procurement.case, because here the
    # cost genuinely is the paper: a forty-page scan costs forty renders,
    # forty burns and forty OCR passes, and a one-page letter costs one.
    # Billing per job would price those identically and let a tenant put
    # an entire archive through as one work item.
    #
    # Metered on the OUTPUT page count, not the source's. They are equal
    # today and the phrasing matters anyway: the output is what was
    # produced, and a source whose page count could not be read is a
    # FAILED job that must bill nothing.
    UsageEventType(
        name="redaction.page",
        unit=UsageUnit.PAGE,
        emission=EmissionKind.OCCURRENCE,
        billable=True,
        default_provider="internal",
        description="One page of sanitized PDF produced by the redaction engine.",
    ),
)'''

# ---------------------------------------------------------------------------
# 4. app/api/capability_gate.py — a readable name in the 402 body
# ---------------------------------------------------------------------------

DISPLAY_NAME_ANCHOR = """_DISPLAY_NAMES = {
    entitlements.RECONCILIATION_CAPABILITY: "Procurement matching",
}"""

DISPLAY_NAME_REPLACEMENT = """_DISPLAY_NAMES = {
    entitlements.RECONCILIATION_CAPABILITY: "Procurement matching",
    # ARCH32-S1:capability-redaction-display. Without an entry here the 402
    # body reads "capability.redaction is included on higher plans", which is
    # a key name in front of a customer.
    entitlements.REDACTION_CAPABILITY: "Document redaction",
}"""

# ---------------------------------------------------------------------------
# 5. requirements.txt — pikepdf
# ---------------------------------------------------------------------------

REQUIREMENTS_ANCHOR = """pypdf==6.14.2
pypdfium2==5.11.0"""

REQUIREMENTS_REPLACEMENT = """pikepdf==10.5.1
pypdf==6.14.2
pypdfium2==5.11.0"""


PATCHES: list[FilePatch] = [
    FilePatch(
        relpath="app/models/__init__.py",
        sentinel=SENTINEL_MODELS_IMPORT,
        edits=[
            Edit(
                anchor=MODELS_IMPORT_ANCHOR,
                replacement=MODELS_IMPORT_REPLACEMENT,
                description="import RedactionJob and RedactionRegion",
            ),
            Edit(
                anchor=MODELS_ALL_ANCHOR,
                replacement=MODELS_ALL_REPLACEMENT,
                description="export the two new models",
            ),
        ],
    ),
    FilePatch(
        relpath="app/core/entitlements.py",
        sentinel=SENTINEL_ENTITLEMENTS,
        edits=[
            Edit(
                anchor=ENTITLEMENTS_EXPORT_ANCHOR,
                replacement=ENTITLEMENTS_EXPORT_REPLACEMENT,
                description="export REDACTION_CAPABILITY",
            ),
            Edit(
                anchor=ENTITLEMENTS_KEY_ANCHOR,
                replacement=ENTITLEMENTS_KEY_REPLACEMENT,
                description="declare the key and add it to CAPABILITY_KEYS",
            ),
            Edit(
                anchor=ENTITLEMENTS_ENTRY_ANCHOR,
                replacement=ENTITLEMENTS_ENTRY_REPLACEMENT,
                description="register the Entitlement row",
            ),
        ],
    ),
    FilePatch(
        relpath="app/core/usage_events.py",
        sentinel=SENTINEL_USAGE_EVENT,
        edits=[
            Edit(
                anchor=USAGE_EVENT_ANCHOR,
                replacement=USAGE_EVENT_REPLACEMENT,
                description="register the redaction.page meter",
            ),
        ],
    ),
    FilePatch(
        relpath="app/api/capability_gate.py",
        sentinel=SENTINEL_DISPLAY_NAME,
        edits=[
            Edit(
                anchor=DISPLAY_NAME_ANCHOR,
                replacement=DISPLAY_NAME_REPLACEMENT,
                description="display name for the 402 body",
            ),
        ],
    ),
    FilePatch(
        relpath="requirements.txt",
        sentinel=SENTINEL_REQUIREMENTS,
        edits=[
            Edit(
                anchor=REQUIREMENTS_ANCHOR,
                replacement=REQUIREMENTS_REPLACEMENT,
                description="pikepdf (MPL-2.0)",
            ),
        ],
    ),
]


class PatchError(RuntimeError):
    pass


def apply_patch(patch: FilePatch, *, check_only: bool) -> str:
    path = BACKEND / patch.relpath
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
                f"{found} time(s), expected {edit.occurrences}. The file is "
                "not in the state this patch was written against; nothing "
                "has been written."
            )
        updated = updated.replace(edit.anchor, edit.replacement, edit.occurrences)

    if patch.sentinel not in updated:
        raise PatchError(
            f"{patch.relpath}: sentinel {patch.sentinel!r} is absent from the "
            "patched text. A sentinel must be a substring of what its own "
            "patch writes, or the next run re-applies the edit."
        )

    if check_only:
        return f"WOULD {patch.relpath}: {len(patch.edits)} edit(s)"

    _write(path, updated, newline, had_bom)
    return f"WROTE {patch.relpath}: {len(patch.edits)} edit(s)"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check", action="store_true", help="report without writing anything"
    )
    args = parser.parse_args()

    print("ARCH-32 Step 1 — modified-file patches")
    print(f"backend: {BACKEND}")
    print()

    results: list[str] = []
    try:
        for patch in PATCHES:
            results.append(apply_patch(patch, check_only=args.check))
    except PatchError as exc:
        for line in results:
            print(f"  {line}")
        print(f"\n  FAIL  {exc}")
        return 1

    for line in results:
        print(f"  {line}")

    pending = sum(1 for line in results if line.startswith(("WOULD", "WROTE")))
    print()
    if args.check:
        print(f"{pending} file(s) would change. Nothing was written.")
    else:
        print(f"{pending} file(s) changed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
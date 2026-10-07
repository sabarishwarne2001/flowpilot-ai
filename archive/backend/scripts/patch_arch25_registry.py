#!/usr/bin/env python
"""ARCH-25 — register the new models and the new audit vocabulary.

    python scripts/patch_arch25_registry.py
    python scripts/patch_arch25_registry.py --check

WHY A PATCH SCRIPT RATHER THAN TWO REWRITTEN FILES
==================================================

`app/models/__init__.py` is 463 lines of import and `__all__` that every other
phase also edits, and `app/models/audit_log.py` is the single most-referenced
model in the schema. Both need six-line insertions. Reproducing them in full
invites a transcription error in the 457 lines that were not supposed to
change, and a diff reviewer cannot tell the difference. This is the ARCH-19
precedent: anchored, idempotent, and loud on a miss.

EVERY EDIT IS ANCHORED AND IDEMPOTENT
=====================================

Each edit locates an exact existing string and inserts after it. If an anchor
is absent the script EXITS NON-ZERO and changes nothing — it does not guess,
and it does not append to the end of the file hoping for the best. If the
inserted text is already present the edit is skipped, so re-running after a
partial failure is safe.

WHY THE PYTHON ENUM MUST MATCH THE POSTGRESQL TYPE
==================================================

`arch25_step1_branding_vocabulary` adds four actions and two resource types to
`audit_action` and `audit_resource_type`. If `AuditAction` here does not gain
the same members, SQLAlchemy cannot round-trip a row the database happily
accepted: the write succeeds through raw SQL and the read raises
`LookupError` on the enum. verify_arch25.py G2 asserts both sides agree, in
the same shape as verify_arch22.py G16.
"""

from __future__ import annotations

import argparse
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]

AUDIT_LOG = ROOT / "app" / "models" / "audit_log.py"
MODELS_INIT = ROOT / "app" / "models" / "__init__.py"


class AnchorMissing(RuntimeError):
    """An expected anchor was not found. The file has moved on; stop."""


# ---------------------------------------------------------------------------
# Edits
# ---------------------------------------------------------------------------

AUDIT_RESOURCE_ANCHOR = '    MODEL_ROUTE = "MODEL_ROUTE"\n'
AUDIT_RESOURCE_INSERT = """    # ARCH-25 — white-label. Added to the PostgreSQL type by
    # arch25_step1_branding_vocabulary; this enum must stay in step with it.
    CUSTOM_DOMAIN = "CUSTOM_DOMAIN"
    TENANT_BRANDING = "TENANT_BRANDING"
"""

AUDIT_ACTION_ANCHOR = '    FALLBACK_POLICY_CHANGED = "FALLBACK_POLICY_CHANGED"\n'
AUDIT_ACTION_INSERT = """    # ARCH-25 — white-label.
    #
    # DOMAIN_VERIFIED is the event that unlocks certificate issuance, and
    # TLS_ISSUED records a certificate now existing for a customer-controlled
    # hostname. Both are things an incident review filters on directly, which
    # is why neither is an UPDATED carrying a details payload.
    #
    # A lapsed sender domain reuses DISABLED rather than adding a fifth
    # action: the visibility invariant is carried by
    # tenant_branding.sender_domain_status = 'LAPSED', and a second
    # vocabulary for one event makes the audit log harder to read, not easier.
    DOMAIN_VERIFIED = "DOMAIN_VERIFIED"
    DOMAIN_REVOKED = "DOMAIN_REVOKED"
    TLS_ISSUED = "TLS_ISSUED"
    BRANDING_UPDATED = "BRANDING_UPDATED"
"""

MODELS_IMPORT_ANCHOR = """from app.models.byok import (
    TenantModelRoute,
    TenantProviderCredential,
)
"""
MODELS_IMPORT_INSERT = """from app.models.custom_domain import (
    CERTIFICATE_STATUS_VALUES,
    CHALLENGE_LABEL,
    CUSTOM_DOMAIN_STATUS_VALUES,
    RESOLVABLE_DOMAIN_STATUSES,
    CustomDomain,
)
from app.models.tenant_branding import (
    BRANDING_COLOR_TOKENS,
    COLOR_SCHEME_VALUES,
    SENDABLE_SENDER_STATUSES,
    SENDER_DOMAIN_STATUS_VALUES,
    TenantBranding,
)
"""

MODELS_ALL_ANCHOR = '    "empty_buckets",\n]'
MODELS_ALL_INSERT = """    "empty_buckets",
    # ARCH-25 — white-label, custom domains and tenant branding.
    "CustomDomain",
    "TenantBranding",
    "CUSTOM_DOMAIN_STATUS_VALUES",
    "CERTIFICATE_STATUS_VALUES",
    "RESOLVABLE_DOMAIN_STATUSES",
    "CHALLENGE_LABEL",
    "SENDER_DOMAIN_STATUS_VALUES",
    "SENDABLE_SENDER_STATUSES",
    "COLOR_SCHEME_VALUES",
    "BRANDING_COLOR_TOKENS",
]"""


EDITS: tuple[tuple[pathlib.Path, str, str, str, str], ...] = (
    (
        AUDIT_LOG,
        "AuditResourceType gains CUSTOM_DOMAIN and TENANT_BRANDING",
        AUDIT_RESOURCE_ANCHOR,
        AUDIT_RESOURCE_ANCHOR + AUDIT_RESOURCE_INSERT,
        'CUSTOM_DOMAIN = "CUSTOM_DOMAIN"',
    ),
    (
        AUDIT_LOG,
        "AuditAction gains the four ARCH-25 actions",
        AUDIT_ACTION_ANCHOR,
        AUDIT_ACTION_ANCHOR + AUDIT_ACTION_INSERT,
        'DOMAIN_VERIFIED = "DOMAIN_VERIFIED"',
    ),
    (
        MODELS_INIT,
        "models/__init__ imports CustomDomain and TenantBranding",
        MODELS_IMPORT_ANCHOR,
        MODELS_IMPORT_ANCHOR + MODELS_IMPORT_INSERT,
        "from app.models.custom_domain import",
    ),
    (
        MODELS_INIT,
        "models/__init__ exports the ARCH-25 names",
        MODELS_ALL_ANCHOR,
        MODELS_ALL_INSERT,
        '    "CustomDomain",',
    ),
)


def _read(path: pathlib.Path) -> str:
    """utf-8-sig: app/schemas/usage.py still carries a BOM upstream, and any
    reader in this repository that assumes plain utf-8 eventually meets it."""
    return path.read_text(encoding="utf-8-sig")


def apply(check_only: bool) -> int:
    applied = 0
    skipped = 0
    contents: dict[pathlib.Path, str] = {}

    for path, label, anchor, replacement, marker in EDITS:
        if not path.exists():
            print(f"[FAIL] {label}: {path} does not exist")
            return 1

        source = contents.get(path)
        if source is None:
            source = _read(path)

        if marker in source:
            print(f"[skip] {label} — already present")
            skipped += 1
            contents[path] = source
            continue

        occurrences = source.count(anchor)
        if occurrences != 1:
            print(
                f"[FAIL] {label}: anchor found {occurrences} times in "
                f"{path.name}, expected exactly 1. Nothing was written."
            )
            print(f"       anchor: {anchor.strip()[:78]!r}")
            return 1

        contents[path] = source.replace(anchor, replacement, 1)
        print(f"[ ok ] {label}")
        applied += 1

    if check_only:
        print(f"\n--check: {applied} edit(s) would be applied, {skipped} already present.")
        return 1 if applied else 0

    for path, text in contents.items():
        path.write_text(text, encoding="utf-8", newline="\n")

    print(f"\n{applied} edit(s) applied, {skipped} already present.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="ARCH-25 registry patch")
    parser.add_argument(
        "--check",
        action="store_true",
        help="Report what would change and exit non-zero if anything would.",
    )
    args = parser.parse_args()

    print("=" * 72)
    print("ARCH-25 — model registry and audit vocabulary")
    print("=" * 72)
    return apply(args.check)


if __name__ == "__main__":
    raise SystemExit(main())
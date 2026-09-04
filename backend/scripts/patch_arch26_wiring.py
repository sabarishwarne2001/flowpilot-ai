"""ARCH-26 wiring — anchored, idempotent patches to seven existing files.

    python scripts/patch_arch26_wiring.py
    python scripts/patch_arch26_wiring.py --check

Established as the ARCH-19 precedent and reused by ARCH-23 and ARCH-25: for a
large file receiving a surgical change, an anchored patch that fails loudly on
a missing anchor is safer to review and safer to re-run than a full rewrite,
because a full rewrite silently reverts anything that landed in the file
between the two phases.

Every patch here is idempotent — it checks for its own marker first — so
running this twice is a no-op and running it after a partial failure completes
the rest. `--check` reports what would change without writing.

FILES TOUCHED
=============
    backend/requirements.txt              pyarrow pin (decision B2-a)
    app/core/encryption.py                encrypt_secret/decrypt_secret (B1-a)
    app/models/audit_log.py               three resource types, five actions
    app/models/__init__.py                three model exports
    app/workers/profiles.py               two job types on LIGHT
    app/workers/handlers/__init__.py      two handler registrations
    app/api/v1/router.py                  mount the analytics router

THE PROFILE AND HANDLER PATCHES ARE NOT OPTIONAL BOOKKEEPING
============================================================

`assert_imports_match_profile()` runs `uncovered_job_types()` at EVERY
worker's startup and raises `ProfileError` on a handler no profile claims.
Registering `analytics.export_sync` in handlers/__init__.py without adding it
to LIGHT in profiles.py stops the entire fleet booting — the same defect
ARCH-16 shipped, ARCH-25 recorded in a comment, and this script exists partly
to make impossible to repeat.
"""

from __future__ import annotations

import argparse
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]

CHECK_ONLY = False
_applied: list[str] = []
_skipped: list[str] = []
_failed: list[str] = []


class AnchorMissing(RuntimeError):
    """An anchor was not found. The file has moved on; stop rather than guess."""


def _read(relative: str) -> tuple[pathlib.Path, str]:
    path = ROOT / relative
    if not path.exists():
        raise AnchorMissing(f"{relative} does not exist.")
    # utf-8-sig: app/schemas/usage.py carries a pre-existing UTF-8 BOM and the
    # same reader is used across every ARCH-0V-era script for consistency.
    return path, path.read_text(encoding="utf-8-sig")


def _write(path: pathlib.Path, text: str) -> None:
    if CHECK_ONLY:
        return
    path.write_text(text, encoding="utf-8")


def patch(
    relative: str, *, marker: str, anchor: str, replacement: str
) -> None:
    """Insert `replacement` in place of `anchor`, once.

    `marker` is a string that exists only after the patch has been applied.
    Checking it rather than checking for the replacement itself means a patch
    whose replacement was later hand-edited is still recognised as applied.
    """
    try:
        path, source = _read(relative)
    except AnchorMissing as exc:
        _failed.append(f"{relative}: {exc}")
        return

    if marker in source:
        _skipped.append(f"{relative} (already applied)")
        return

    if anchor not in source:
        _failed.append(
            f"{relative}: anchor not found. Expected to find:\n"
            f"    {anchor.splitlines()[0][:100]}"
        )
        return

    if source.count(anchor) != 1:
        _failed.append(
            f"{relative}: anchor appears {source.count(anchor)} times; "
            "refusing to guess which one."
        )
        return

    _write(path, source.replace(anchor, replacement))
    _applied.append(relative)


# ---------------------------------------------------------------------------
# 1. requirements.txt — pyarrow (decision B2-a)
# ---------------------------------------------------------------------------
#
# Placed alphabetically between pypdfium2 and PyPika, matching the file's
# existing ordering so a future `pip freeze` diff stays readable.
#
# 21.0.0 is the last release compatible with the pinned numpy==2.3.5 without
# forcing a numpy bump, and pandas==3.0.3 (already pinned, currently unused by
# app/) declares pyarrow as a hard dependency — so this pin also closes a real
# gap in the freeze rather than only serving ARCH-26.

patch(
    "requirements.txt",
    marker="pyarrow==",
    anchor="pypdfium2==5.11.0\n",
    replacement="pypdfium2==5.11.0\npyarrow==21.0.0\n",
)


# ---------------------------------------------------------------------------
# 2. app/core/encryption.py — large-secret path (decision B1-a)
# ---------------------------------------------------------------------------

_ENCRYPTION_CONSTANTS = '''
# ---------------------------------------------------------------------------
# ARCH-26 — large secrets (audit decision B1-a)
#
# The two bounds above exist because the columns they protect are String(512):
# SMTP passwords, webhook signing secrets, provider API keys. A ciphertext
# that outgrows its column is a defect worth catching at encrypt time.
#
# A warehouse credential does not fit that shape. A BigQuery service-account
# JSON is ~2.3KB and a Snowflake PKCS#8 private key ~1.7KB, so both exceed
# MAX_PLAINTEXT_LENGTH by an order of magnitude.
#
# Raising MAX_PLAINTEXT_LENGTH would have been one line and the wrong one: it
# silently widens the guard protecting every String(512) column above, and the
# first oversized SMTP password then fails at INSERT with a database error
# rather than at encrypt with a message naming the field. So the large-secret
# path is a separate pair of functions with its own ceiling, writing into
# unbounded Text columns.
# ---------------------------------------------------------------------------

#: 16KB. Comfortably above the largest real credential (a service-account JSON
#: with a 4096-bit key is ~3.2KB) and far below anything that would make a
#: single row awkward. A value this size is a credential; a value larger than
#: this is a mistake, and refusing it is more useful than storing it.
MAX_SECRET_PLAINTEXT_LENGTH = 16384
'''

patch(
    "app/core/encryption.py",
    marker="MAX_SECRET_PLAINTEXT_LENGTH",
    anchor="MAX_CIPHERTEXT_LENGTH = 512\nMAX_PLAINTEXT_LENGTH = 300\n",
    replacement=(
        "MAX_CIPHERTEXT_LENGTH = 512\nMAX_PLAINTEXT_LENGTH = 300\n"
        + _ENCRYPTION_CONSTANTS
    ),
)

_ENCRYPTION_FUNCTIONS = '''def encrypt_secret(plaintext: str) -> str:
    """Encrypt a large credential for a Text column (ARCH-26).

    Same MultiFernet key set as `encrypt_password`, so a key rotation covers
    both and `rotate_ciphertext` works on either. The differences are the
    plaintext ceiling and the absence of a ciphertext ceiling: the destination
    column is Text, so there is no storage bound to enforce.
    """
    if plaintext is None:
        raise ValueError("Cannot encrypt None")
    if not isinstance(plaintext, str):
        raise ValueError("encrypt_secret expects str")
    if len(plaintext) > MAX_SECRET_PLAINTEXT_LENGTH:
        raise CiphertextTooLongError(
            f"Secret exceeds {MAX_SECRET_PLAINTEXT_LENGTH} characters. A "
            "credential this large is almost certainly a mistake."
        )

    multi, _ = _get()
    return multi.encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt_secret(ciphertext: str) -> str:
    """Inverse of `encrypt_secret`.

    Distinct from `decrypt_password` only in name. Both are kept so a reader
    of a call site can tell which column class is involved without following
    the value back to its table.
    """
    if not ciphertext:
        raise DecryptionError("Empty ciphertext")

    multi, _ = _get()
    try:
        return multi.decrypt(ciphertext.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError, TypeError) as exc:
        raise DecryptionError(
            "Ciphertext could not be decrypted under any configured key."
        ) from exc


def secret_fingerprint(plaintext: str) -> str:
    """First 12 hex of SHA-256 over the plaintext.

    Displayable. It lets a tenant confirm which key is installed without the
    key being readable back, and it lets an operator confirm two destinations
    share a credential without either being disclosed.

    12 hex characters is 48 bits. That is not a collision-resistant identifier
    and is not used as one — it labels a value the holder already possesses.
    """
    import hashlib

    if plaintext is None:
        raise ValueError("Cannot fingerprint None")
    return hashlib.sha256(plaintext.encode("utf-8")).hexdigest()[:12]


'''

patch(
    "app/core/encryption.py",
    marker="def encrypt_secret(",
    anchor="def decrypting_key_index(ciphertext: str) -> Optional[int]:",
    replacement=(
        _ENCRYPTION_FUNCTIONS
        + "def decrypting_key_index(ciphertext: str) -> Optional[int]:"
    ),
)


# ---------------------------------------------------------------------------
# 3. app/models/audit_log.py — vocabulary, mirroring arch26_step1
# ---------------------------------------------------------------------------

patch(
    "app/models/audit_log.py",
    marker="WAREHOUSE_DESTINATION",
    anchor='''    CUSTOM_DOMAIN = "CUSTOM_DOMAIN"
    TENANT_BRANDING = "TENANT_BRANDING"''',
    replacement='''    CUSTOM_DOMAIN = "CUSTOM_DOMAIN"
    TENANT_BRANDING = "TENANT_BRANDING"
    # ARCH-26 — enterprise analytics and BI egress. Added to the PostgreSQL
    # type by arch26_step1_export_vocabulary; this enum must stay in step with
    # it. verify_arch26.py G2 asserts both sides agree.
    WAREHOUSE_DESTINATION = "WAREHOUSE_DESTINATION"
    EXPORT_SCHEDULE = "EXPORT_SCHEDULE"
    EXPORT_SYNC_RUN = "EXPORT_SYNC_RUN"''',
)

patch(
    "app/models/audit_log.py",
    marker="DESTINATION_CREATED",
    anchor='''    TLS_ISSUED = "TLS_ISSUED"
    BRANDING_UPDATED = "BRANDING_UPDATED"''',
    replacement='''    TLS_ISSUED = "TLS_ISSUED"
    BRANDING_UPDATED = "BRANDING_UPDATED"
    # ARCH-26 — enterprise analytics and BI egress.
    #
    # EXPORTED is deliberately NOT reused for a warehouse push. ARCH-20 emits
    # it when an operator downloads a compliance bundle: a human pulling data
    # out under a legal obligation. A warehouse sync is a scheduled machine
    # push into infrastructure the tenant controls, and a reviewer filtering
    # EXPORTED to answer "what left by human hand?" must not have to subtract
    # several thousand cron-driven rows to get the answer.
    DESTINATION_CREATED = "DESTINATION_CREATED"
    DESTINATION_TESTED = "DESTINATION_TESTED"
    SYNC_TRIGGERED = "SYNC_TRIGGERED"
    SYNC_COMPLETED = "SYNC_COMPLETED"
    SYNC_FAILED = "SYNC_FAILED"''',
)


# ---------------------------------------------------------------------------
# 4. app/models/__init__.py
# ---------------------------------------------------------------------------

patch(
    "app/models/__init__.py",
    marker="warehouse_sync",
    anchor="from app.models.usage_rollup import",
    replacement='''from app.models.warehouse_sync import (  # noqa: F401
    ExportSchedule,
    ExportSyncRun,
    WarehouseDestination,
)
from app.models.usage_rollup import''',
)


# ---------------------------------------------------------------------------
# 5. app/workers/profiles.py — LIGHT
# ---------------------------------------------------------------------------

patch(
    "app/workers/profiles.py",
    marker="analytics.export_sync",
    anchor='''            "domain.verify_dns",
            "tls.renew_sweep",
        }''',
    replacement='''            "domain.verify_dns",
            "tls.renew_sweep",
            # ARCH-26 analytics egress. Parquet generation and an HTTPS push
            # to a tenant warehouse — no heavy ML imports, so the thin image
            # is the right home. pyarrow is a build dependency of the image,
            # not a member of HEAVY_MODULES.
            #
            # As with the two entries above, this is not optional bookkeeping:
            # assert_imports_match_profile() raises ProfileError at every
            # worker's startup on a handler no profile claims, so registering
            # these in handlers/__init__.py without adding them here stops the
            # entire fleet booting.
            "analytics.export_sync",
            "analytics.warehouse_push",
        }''',
)


# ---------------------------------------------------------------------------
# 6. app/workers/handlers/__init__.py
# ---------------------------------------------------------------------------

patch(
    "app/workers/handlers/__init__.py",
    marker="ARCH26_JOB_TYPES",
    anchor='''ARCH25_JOB_TYPES: frozenset[str] = frozenset(
    {"domain.verify_dns", "tls.renew_sweep"}
)''',
    replacement='''ARCH25_JOB_TYPES: frozenset[str] = frozenset(
    {"domain.verify_dns", "tls.renew_sweep"}
)
ARCH26_JOB_TYPES: frozenset[str] = frozenset(
    {"analytics.export_sync", "analytics.warehouse_push"}
)''',
)

patch(
    "app/workers/handlers/__init__.py",
    marker="_analytics_export_sync",
    anchor='''_HANDLERS = {''',
    replacement='''def _analytics_export_sync(payload: dict[str, Any]) -> dict[str, Any]:
    from app.workers.handlers.analytics import handle_export_sync
    return handle_export_sync(payload)


def _analytics_warehouse_push(payload: dict[str, Any]) -> dict[str, Any]:
    from app.workers.handlers.analytics import handle_warehouse_push
    return handle_warehouse_push(payload)


_HANDLERS = {''',
)

patch(
    "app/workers/handlers/__init__.py",
    marker='"analytics.export_sync": _analytics_export_sync',
    anchor='''    "domain.verify_dns": _domain_verify_dns,
    "tls.renew_sweep": _tls_renew_sweep,
}''',
    replacement='''    "domain.verify_dns": _domain_verify_dns,
    "tls.renew_sweep": _tls_renew_sweep,
    # ARCH-26. Both are also listed on the LIGHT profile in
    # app/workers/profiles.py; a handler here with no profile there is a job
    # that enqueues cleanly and never runs.
    "analytics.export_sync": _analytics_export_sync,
    "analytics.warehouse_push": _analytics_warehouse_push,
}''',
)

patch(
    "app/workers/handlers/__init__.py",
    marker='"ARCH26_JOB_TYPES",',
    anchor='''    "ARCH25_JOB_TYPES",''',
    replacement='''    "ARCH25_JOB_TYPES",
    "ARCH26_JOB_TYPES",''',
)


# ---------------------------------------------------------------------------
# 7. app/api/v1/router.py
# ---------------------------------------------------------------------------

patch(
    "app/api/v1/router.py",
    marker="warehouse_sync,",
    anchor="    verifications,\n    work_items,\n    workspaces,\n)",
    replacement=(
        "    verifications,\n    warehouse_sync,\n    work_items,\n"
        "    workspaces,\n)"
    ),
)

patch(
    "app/api/v1/router.py",
    marker="warehouse_sync.router",
    anchor="api_router.include_router(admin_cogs.router)",
    replacement='''# ARCH-26 Enterprise Analytics, BI Egress & Warehouse Sync.
#
# One router. Unlike ARCH-25's domain/branding split, the role boundary here
# is uniform — reads ADMIN, writes OWNER — and is carried by per-endpoint
# dependencies rather than by which router an endpoint landed in.
api_router.include_router(warehouse_sync.router)

api_router.include_router(admin_cogs.router)''',
)


# ---------------------------------------------------------------------------


def main() -> int:
    global CHECK_ONLY

    parser = argparse.ArgumentParser(description="ARCH-26 wiring patches")
    parser.add_argument(
        "--check",
        action="store_true",
        help="report what would change without writing",
    )
    args = parser.parse_args()
    CHECK_ONLY = args.check

    # The patches above run at import time so that --check and apply share one
    # code path. Nothing below re-runs them; this only reports.
    for entry in _applied:
        print(f"  {'WOULD PATCH' if CHECK_ONLY else 'PATCHED'}  {entry}")
    for entry in _skipped:
        print(f"  SKIP     {entry}")
    for entry in _failed:
        print(f"  FAILED   {entry}", file=sys.stderr)

    print(
        f"\n{len(_applied)} applied, {len(_skipped)} already present, "
        f"{len(_failed)} failed."
    )
    if _failed:
        print(
            "\nAn anchor was missing. That means the target file has changed "
            "since ARCH-26 was written. Do NOT hand-apply blindly — re-read "
            "the file and update the anchor in this script, so the next run "
            "is still idempotent.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
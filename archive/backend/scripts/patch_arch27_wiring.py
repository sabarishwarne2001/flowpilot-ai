"""ARCH-27 wiring — anchored, idempotent patches to six existing files.

    python scripts/patch_arch27_wiring.py
    python scripts/patch_arch27_wiring.py --check

The ARCH-19 precedent, reused by ARCH-23, ARCH-25 and ARCH-26: for a large
shared file receiving a surgical change, an anchored patch that fails loudly
on a missing anchor is safer to review and safer to re-run than a full
rewrite, because a full rewrite silently reverts anything that landed in the
file between the two phases.

Every patch checks for its own marker first, so running this twice is a no-op
and running it after a partial failure completes the rest.

FILES TOUCHED
=============
    alembic/env.py                    autogenerate drift filtering (ARCH-26 CF1)
    app/models/audit_log.py           four resource types, five actions
    app/models/__init__.py            ten model exports
    app/workers/profiles.py           two job types on LIGHT
    app/workers/handlers/__init__.py  ARCH27_JOB_TYPES + orphan cleanup (CF2)
    app/api/v1/router.py              mount the partner and marketplace routers

THE PROFILE AND HANDLER PATCHES ARE NOT OPTIONAL BOOKKEEPING
============================================================

`assert_imports_match_profile()` runs `uncovered_job_types()` at EVERY
worker's startup and raises `ProfileError` on a handler no profile claims.
Registering `partner.rev_share_compute` in handlers/__init__.py without adding
it to LIGHT in profiles.py stops the entire fleet booting — the defect ARCH-16
shipped, ARCH-25 recorded in a comment, and ARCH-26 wrote a script partly to
make impossible to repeat.

CARRIED-FORWARD RESOLUTION 2 — THE ORPHANED GUARDS
==================================================

`ARCH16_JOB_TYPES`, `ARCH25_JOB_TYPES` and `ARCH26_JOB_TYPES` are exported
from handlers/__init__.py with zero call sites; `verify_arch26.py` notices and
does not fix it. This script introduces `ALL_PHASE_JOB_TYPES` — a union that
`register_all()` asserts against `_HANDLERS` at import — so all four constants
have a real consumer and a drift between the declared vocabulary and the
registry fails loudly instead of sitting there.

That is the general remedy for the recurring defect class in this codebase:
correct logic implemented as a module-level export with no caller, invisible
to linters. A constant nothing reads is a comment with a type annotation.
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


def patch(relative: str, *, marker: str, anchor: str, replacement: str) -> None:
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
# 1. alembic/env.py — autogenerate drift filtering (ARCH-26 carried forward)
# ---------------------------------------------------------------------------
#
# Three classes of false positive make `alembic revision --autogenerate`
# produce a non-empty diff against a schema nobody changed:
#
#   Raw partition tables. arch11_step2 creates document_chunks_p00..p15 with
#   op.execute. They are real tables PostgreSQL reflects, and no model maps
#   them, so autogenerate proposes dropping sixteen partitions of the chunk
#   store on every run.
#
#   The billable_seats VIEW. arch15_step3 creates it; app/models/billable_seat
#   maps it read-only. Reflection reports a view as a table, and autogenerate
#   proposes dropping it.
#
#   Comment flapping. Several ARCH-24/25/26 migrations attach column comments
#   that the model side does not restate, so every run proposes a comment
#   change in both directions. `include_object` cannot suppress these — a
#   comment is not an object — so they are stripped from the generated
#   directives instead.
#
# The filter is deliberately narrow. `reflected and not compare_to` means it
# only ever suppresses "this exists in the database and not in the models",
# never the reverse: a table a model declares and a migration forgot still
# shows up, which is the drift worth catching.

ENV_HOOKS = '''
_PARTITION_TABLE_PREFIX = "document_chunks_p"

#: Database objects that exist by design and are not mapped by any model.
#: Reflected-only, so a model-side object that a migration forgot still
#: appears in the diff — which is the drift worth catching.
UNMANAGED_TABLES: frozenset[str] = frozenset({"billable_seats"})


def include_object(obj, name, type_, reflected, compare_to):
    """Keep autogenerate honest by hiding only what is deliberately unmapped.

    `reflected and compare_to is None` is the whole guard: it suppresses
    "present in the database, absent from the models" and never the reverse.
    A table declared on a model and missing from a migration still produces a
    diff, because that is a real defect and this hook must not hide it.
    """
    if type_ == "table" and reflected and compare_to is None:
        if name.startswith(_PARTITION_TABLE_PREFIX):
            # arch11_step2 creates these with op.execute; they are partitions
            # of document_chunks, not independent tables.
            return False
        if name in UNMANAGED_TABLES:
            # billable_seats is a VIEW. Reflection reports it as a table.
            return False
    return True


def _strip_comment_only_ops(directives) -> None:
    """Drop comment-only alterations from a generated revision.

    Column and table comments live in the migrations and are not restated on
    the model side, so every autogenerate run proposes changing them back and
    forth. `include_object` cannot help: a comment is not an object.

    This removes the comment ops and then removes any ModifyTableOps left
    empty by the removal — an empty ModifyTableOps still renders as a
    `with op.batch_alter_table(...)` block containing nothing.
    """
    from alembic.operations import ops as alembic_ops

    comment_op_names = (
        "CreateTableCommentOp",
        "DropTableCommentOp",
    )
    comment_ops = tuple(
        getattr(alembic_ops, attr)
        for attr in comment_op_names
        if hasattr(alembic_ops, attr)
    )

    for directive in directives:
        for upgrade_ops in getattr(directive, "upgrade_ops_list", []) or [
            getattr(directive, "upgrade_ops", None)
        ]:
            if upgrade_ops is None:
                continue
            surviving = []
            for op_ in upgrade_ops.ops:
                if comment_ops and isinstance(op_, comment_ops):
                    continue
                if isinstance(op_, alembic_ops.ModifyTableOps):
                    inner = [
                        sub
                        for sub in op_.ops
                        if not (comment_ops and isinstance(sub, comment_ops))
                        and not _is_comment_only_alter(sub)
                    ]
                    if not inner:
                        continue
                    op_.ops = inner
                surviving.append(op_)
            upgrade_ops.ops = surviving


def _is_comment_only_alter(op_) -> bool:
    """True for an AlterColumnOp whose only change is the comment."""
    from alembic.operations import ops as alembic_ops

    if not isinstance(op_, alembic_ops.AlterColumnOp):
        return False
    touches_comment = (
        op_.kw.get("comment") is not None
        or op_.kw.get("existing_comment") is not None
    )
    touches_anything_else = (
        op_.modify_type is not None
        or op_.modify_nullable is not None
        or op_.modify_server_default is not False
        or op_.modify_name is not None
    )
    return touches_comment and not touches_anything_else


def process_revision_directives(context_, revision, directives) -> None:
    _strip_comment_only_ops(directives)


'''

patch(
    "alembic/env.py",
    marker="UNMANAGED_TABLES",
    anchor=(
        "# Bind Base metadata to allow Alembic to read active ORM schemas "
        "during autogenerate runs\ntarget_metadata = Base.metadata\n"
    ),
    replacement=(
        "# Bind Base metadata to allow Alembic to read active ORM schemas "
        "during autogenerate runs\ntarget_metadata = Base.metadata\n"
        + ENV_HOOKS
    ),
)

patch(
    "alembic/env.py",
    marker='dialect_opts={"paramstyle": "named"},\n        include_object=include_object,',
    anchor=(
        '        literal_binds=True,\n'
        '        dialect_opts={"paramstyle": "named"},\n'
        '    )'
    ),
    replacement=(
        '        literal_binds=True,\n'
        '        dialect_opts={"paramstyle": "named"},\n'
        '        include_object=include_object,\n'
        '        process_revision_directives=process_revision_directives,\n'
        '        compare_type=True,\n'
        '    )'
    ),
)

patch(
    "alembic/env.py",
    marker="connection=connection,\n            target_metadata=target_metadata,\n            include_object=include_object,",
    anchor=(
        "        context.configure(\n"
        "            connection=connection, \n"
        "            target_metadata=target_metadata\n"
        "        )"
    ),
    replacement=(
        "        context.configure(\n"
        "            connection=connection,\n"
        "            target_metadata=target_metadata,\n"
        "            include_object=include_object,\n"
        "            process_revision_directives=process_revision_directives,\n"
        "            compare_type=True,\n"
        "        )"
    ),
)


# ---------------------------------------------------------------------------
# 2. app/models/audit_log.py — the ARCH-27 vocabulary
# ---------------------------------------------------------------------------

patch(
    "app/models/audit_log.py",
    marker='PARTNER = "PARTNER"',
    anchor='    EXPORT_SYNC_RUN = "EXPORT_SYNC_RUN"',
    replacement='''    EXPORT_SYNC_RUN = "EXPORT_SYNC_RUN"
    # ARCH-27 — partner marketplace and reseller tenancy. Added to the
    # PostgreSQL type by arch27_step1_partner_vocabulary; this enum must stay
    # in step with it. verify_arch27.py G2 asserts both sides agree.
    #
    # Four, not seven. MARKETPLACE_ITEM covers manifests, signatures and
    # installations: unlike ARCH-26's destination/schedule/run split, where run
    # rows arrive orders of magnitude more often and would drown the credential
    # rows, a catalog item and its versions share a lifetime and a reader.
    # `details.manifest_id` and `details.installation_id` carry the finer grain.
    PARTNER = "PARTNER"
    PARTNER_AGREEMENT = "PARTNER_AGREEMENT"
    REV_SHARE_LEDGER = "REV_SHARE_LEDGER"
    MARKETPLACE_ITEM = "MARKETPLACE_ITEM"''',
)

patch(
    "app/models/audit_log.py",
    marker='PARTNER_CREATED = "PARTNER_CREATED"',
    anchor='    SYNC_FAILED = "SYNC_FAILED"',
    replacement='''    SYNC_FAILED = "SYNC_FAILED"
    # ARCH-27 — partner marketplace and reseller tenancy.
    #
    # CREATED is deliberately NOT reused for PARTNER_CREATED. A reseller tier
    # gaining standing over customer accounts is the row somebody reaches for
    # when asking "when did a third party acquire authority over these
    # tenants?", and CREATED cannot answer it without filtering out every work
    # item, webhook and API key ever made.
    #
    # TENANT_ASSIGNED is emitted on BOTH directions with `details.direction`
    # carrying which. A release is the more interesting event: a burst of
    # assign/release pairs against varying organizations is what book-scope
    # probing looks like.
    #
    # MANIFEST_INSTALLED is the highest-consequence row in the phase — a
    # tenant admitting third-party workflow code into their own automation
    # engine — and shares an action with nothing else.
    PARTNER_CREATED = "PARTNER_CREATED"
    TENANT_ASSIGNED = "TENANT_ASSIGNED"
    REV_SHARE_SETTLED = "REV_SHARE_SETTLED"
    MANIFEST_PUBLISHED = "MANIFEST_PUBLISHED"
    MANIFEST_INSTALLED = "MANIFEST_INSTALLED"''',
)


# ---------------------------------------------------------------------------
# 3. app/models/__init__.py — model registry
# ---------------------------------------------------------------------------
#
# Registration matters beyond tidiness: Base.metadata is what
# `alembic revision --autogenerate` compares against, so a model absent here
# is a model whose table autogenerate proposes DROPPING on the next run.

patch(
    "app/models/__init__.py",
    marker="from app.models.partner import",
    anchor="from app.models.custom_domain import (",
    replacement='''from app.models.partner import (  # noqa: F401
    MarketplaceInstallation,
    MarketplaceItem,
    MarketplaceManifest,
    MarketplaceSignature,
    Partner,
    PartnerMember,
    PartnerMemberRole,
    PartnerOrganization,
    PartnerPayoutPeriod,
    PartnerRevShareAgreement,
    PartnerRevShareLedger,
    PartnerSigningKey,
    RevShareBasisClass,
    PAYABLE_BASIS_CLASSES,
    REV_SHARE_BASIS_CLASS_VALUES,
    SETTLED_PERIOD_STATUSES,
)
from app.models.custom_domain import (''',
)

patch(
    "app/models/__init__.py",
    marker='"PartnerRevShareLedger",',
    anchor='''    "COLOR_SCHEME_VALUES",
    "BRANDING_COLOR_TOKENS",
]''',
    replacement='''    "COLOR_SCHEME_VALUES",
    "BRANDING_COLOR_TOKENS",
    # ARCH-27 — partner marketplace, reseller tenancy and revenue share.
    "MarketplaceInstallation",
    "MarketplaceItem",
    "MarketplaceManifest",
    "MarketplaceSignature",
    "Partner",
    "PartnerMember",
    "PartnerMemberRole",
    "PartnerOrganization",
    "PartnerPayoutPeriod",
    "PartnerRevShareAgreement",
    "PartnerRevShareLedger",
    "PartnerSigningKey",
    "RevShareBasisClass",
    "PAYABLE_BASIS_CLASSES",
    "REV_SHARE_BASIS_CLASS_VALUES",
    "SETTLED_PERIOD_STATUSES",
]''',
)


# ---------------------------------------------------------------------------
# 4. app/workers/profiles.py — two job types on LIGHT
# ---------------------------------------------------------------------------

patch(
    "app/workers/profiles.py",
    marker='"partner.rev_share_compute"',
    anchor='''            "analytics.export_sync",
            "analytics.warehouse_push",''',
    replacement='''            "analytics.export_sync",
            "analytics.warehouse_push",
            # ARCH-27 partner revenue share. Two SQL sweeps over sealed
            # rollups and a SHA-256 over a canonical payload — no heavy ML
            # imports, so the thin image is the right home.
            #
            # As with every entry above, this is not optional bookkeeping:
            # assert_imports_match_profile() raises ProfileError at every
            # worker's startup on a handler no profile claims, so registering
            # these in handlers/__init__.py without adding them here stops the
            # entire fleet booting.
            "partner.rev_share_compute",
            "partner.rev_share_seal",''',
)


# ---------------------------------------------------------------------------
# 5. app/workers/handlers/__init__.py — registration and orphan cleanup
# ---------------------------------------------------------------------------

patch(
    "app/workers/handlers/__init__.py",
    marker="ARCH27_JOB_TYPES",
    anchor='''ARCH26_JOB_TYPES: frozenset[str] = frozenset(
    {"analytics.export_sync", "analytics.warehouse_push"}
)''',
    replacement='''ARCH26_JOB_TYPES: frozenset[str] = frozenset(
    {"analytics.export_sync", "analytics.warehouse_push"}
)
ARCH27_JOB_TYPES: frozenset[str] = frozenset(
    {"partner.rev_share_compute", "partner.rev_share_seal"}
)

#: Every job type this package claims to register, by phase.
#:
#: ARCH-27 carried-forward resolution 2. Before this, ARCH16_JOB_TYPES,
#: ARCH25_JOB_TYPES and ARCH26_JOB_TYPES were module-level exports with zero
#: consumers — the recurring "orphaned guard" defect class in this codebase:
#: correct declarations that no code path reads, invisible to linters, and
#: therefore free to drift from the registry they claim to describe.
#:
#: `register_all()` now asserts this union equals `_HANDLERS.keys()`, so a
#: phase constant that falls out of step with the handler table fails loudly
#: at import instead of silently documenting a lie.
ALL_PHASE_JOB_TYPES: frozenset[str] = (
    ARCH10_JOB_TYPES
    | ARCH11_JOB_TYPES
    | ARCH12_JOB_TYPES
    | ARCH13_JOB_TYPES
    | ARCH14_JOB_TYPES
    | ARCH15_JOB_TYPES
    | ARCH16_JOB_TYPES
    | ARCH25_JOB_TYPES
    | ARCH26_JOB_TYPES
    | ARCH27_JOB_TYPES
)''',
)

patch(
    "app/workers/handlers/__init__.py",
    marker="_partner_rev_share_compute",
    anchor='''def _analytics_warehouse_push(payload: dict[str, Any]) -> dict[str, Any]:
    from app.workers.handlers.analytics import handle_warehouse_push
    return handle_warehouse_push(payload)''',
    replacement='''def _analytics_warehouse_push(payload: dict[str, Any]) -> dict[str, Any]:
    from app.workers.handlers.analytics import handle_warehouse_push
    return handle_warehouse_push(payload)


def _partner_rev_share_compute(payload: dict[str, Any]) -> dict[str, Any]:
    from app.workers.handlers.partner import handle_rev_share_compute
    from app.db.session import SessionLocal
    with SessionLocal() as db:
        return handle_rev_share_compute(db, payload)


def _partner_rev_share_seal(payload: dict[str, Any]) -> dict[str, Any]:
    from app.workers.handlers.partner import handle_rev_share_seal
    from app.db.session import SessionLocal
    with SessionLocal() as db:
        return handle_rev_share_seal(db, payload)''',
)

patch(
    "app/workers/handlers/__init__.py",
    marker='"partner.rev_share_compute": _partner_rev_share_compute',
    anchor='''    "analytics.export_sync": _analytics_export_sync,
    "analytics.warehouse_push": _analytics_warehouse_push,
}''',
    replacement='''    "analytics.export_sync": _analytics_export_sync,
    "analytics.warehouse_push": _analytics_warehouse_push,
    # ARCH-27. Both are also listed on the LIGHT profile in
    # app/workers/profiles.py; a handler here with no profile there is a job
    # that enqueues cleanly and never runs.
    "partner.rev_share_compute": _partner_rev_share_compute,
    "partner.rev_share_seal": _partner_rev_share_seal,
}


def _assert_vocabulary_matches_registry() -> None:
    """ARCH-27 CF2. Give the per-phase constants a consumer.

    A frozenset nothing reads is a comment with a type annotation. Comparing
    the union against the registry means a job type added to one and not the
    other raises here, at import, naming both sides — rather than surfacing
    weeks later as a queue that never drains.
    """
    registered = frozenset(_HANDLERS)
    undeclared = registered - ALL_PHASE_JOB_TYPES
    unregistered = ALL_PHASE_JOB_TYPES - registered
    if undeclared or unregistered:
        raise RuntimeError(
            "job type vocabulary and handler registry disagree. "
            f"registered but undeclared: {sorted(undeclared)}; "
            f"declared but unregistered: {sorted(unregistered)}. "
            "Update the ARCHnn_JOB_TYPES constant for the owning phase."
        )


_assert_vocabulary_matches_registry()''',
)

patch(
    "app/workers/handlers/__init__.py",
    marker='"ARCH27_JOB_TYPES",',
    anchor='''    "ARCH26_JOB_TYPES",
    "register_all",
]''',
    replacement='''    "ARCH26_JOB_TYPES",
    "ARCH27_JOB_TYPES",
    "ALL_PHASE_JOB_TYPES",
    "register_all",
]''',
)


# ---------------------------------------------------------------------------
# 6. app/api/v1/router.py — mount both routers
# ---------------------------------------------------------------------------

patch(
    "app/api/v1/router.py",
    marker="    marketplace,\n",
    anchor="    identity_admin,\n    me,\n",
    replacement="    identity_admin,\n    marketplace,\n    me,\n    partner,\n",
)

patch(
    "app/api/v1/router.py",
    marker="api_router.include_router(partner.router)",
    anchor="api_router.include_router(admin_cogs.router)",
    replacement='''# ARCH-27 Partner Marketplace, Reseller Tenancy & Revenue Share.
#
# Two routers, and the split is a role boundary rather than a filing
# preference. partner.router authenticates a PARTNER principal — a tier above
# organization, gated by partner_members and, for commercial operations, by
# require_superadmin. marketplace.router authenticates an ORGANIZATION
# principal through the ordinary RequireOrgAdmin/RequireOrgOwner dependencies.
#
# Mounting them together would invite one shared dependency across two
# different subjects, which is how a partner principal ends up satisfying an
# organization check.
api_router.include_router(partner.router)
api_router.include_router(marketplace.router)

api_router.include_router(admin_cogs.router)''',
)


# ---------------------------------------------------------------------------


def main() -> int:
    global CHECK_ONLY

    parser = argparse.ArgumentParser(description="ARCH-27 wiring patches")
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
            "since ARCH-27 was written. Do NOT hand-apply blindly — re-read "
            "the file and update the anchor in this script, so the next run "
            "is still idempotent.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
#!/usr/bin/env python
"""ARCH-20 compliance sweeper — retention purge and export expiry.

    python scripts/sweep_compliance.py --exports
    python scripts/sweep_compliance.py --all
    python scripts/sweep_compliance.py --all --apply

DRY RUN IS THE DEFAULT, and unlike sweep_identity.py / sweep_invitations.py
this script does NOT apply unless told to. That asymmetry is deliberate rather
than an inconsistency: those sweepers delete expired tokens and stale
invitations, which are tombstoned records nobody reads. This one can delete
work items and conversations that are live, because ARCH-20's audit found that
`deleted_at` exists on exactly one table in the schema — `uploaded_files`.
There is no soft-delete state to purge from. Age-based retention destroys rows
that a user could still open, so it takes an explicit --apply every time.

Three further guards on the destructive path:

  * the organization must have auto_purge_enabled = TRUE;
  * the relevant *_retention_days must be non-NULL;
  * --purge must be passed explicitly. --all does not include it.

Audit retention is reported but never executed here. ARCH-07's
fn_audit_logs_prevent_mutation() only permits DELETE for a dedicated sweeper
role on rows older than a hard-coded 400 days, so audit purging belongs to
that role's own tooling and its own database URL, not to this process.
"""

from __future__ import annotations

import argparse
import logging
import pathlib
import sys
from datetime import datetime, timedelta, timezone

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

logger = logging.getLogger("sweep_compliance")

DEFAULT_BATCH_SIZE = 2_000


def _session_factory():
    from app.db.session import SessionLocal

    return SessionLocal


def _policies(session_factory, *, require_auto_purge: bool) -> list[dict]:
    from sqlalchemy import select

    from app.models.compliance import RetentionPolicy
    from app.models.organization import Organization

    with session_factory() as db:
        query = select(RetentionPolicy, Organization).join(
            Organization, Organization.id == RetentionPolicy.organization_id
        )
        if require_auto_purge:
            query = query.where(RetentionPolicy.auto_purge_enabled.is_(True))

        return [
            {
                "organization_id": policy.organization_id,
                "slug": organization.slug,
                "work_item_retention_days": policy.work_item_retention_days,
                "conversation_retention_days": policy.conversation_retention_days,
                "audit_retention_days": policy.audit_retention_days,
                "auto_purge_enabled": policy.auto_purge_enabled,
            }
            for policy, organization in db.execute(query).all()
        ]


def _purge_work_items(
    session_factory,
    *,
    organization_id,
    days: int,
    apply: bool,
    batch_size: int,
) -> int:
    """Age-based purge of work_items and their derived chunks.

    work_items has no organization_id, so the predicate joins through
    workspaces. document_chunks has no FK to work_items either, so the chunks
    are deleted explicitly first — nothing cascades.
    """
    from sqlalchemy import text

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    select_ids = text(
        """
        SELECT wi.id
        FROM work_items wi
        JOIN workspaces w ON w.id = wi.workspace_id
        WHERE w.organization_id = :org
          AND wi.created_at < :cutoff
        LIMIT :batch
        """
    )

    with session_factory() as db:
        total = db.execute(
            text(
                """
                SELECT count(*)
                FROM work_items wi
                JOIN workspaces w ON w.id = wi.workspace_id
                WHERE w.organization_id = :org AND wi.created_at < :cutoff
                """
            ),
            {"org": organization_id, "cutoff": cutoff},
        ).scalar_one()

    if not apply:
        print(f"    [DRY RUN] work_items: {total} row(s) older than {days}d")
        return int(total)

    deleted = 0
    while True:
        with session_factory() as db:
            ids = [
                row[0]
                for row in db.execute(
                    select_ids,
                    {
                        "org": organization_id,
                        "cutoff": cutoff,
                        "batch": batch_size,
                    },
                ).fetchall()
            ]
            if not ids:
                break
            db.execute(
                text("DELETE FROM document_chunks WHERE work_item_id = ANY(:ids)"),
                {"ids": ids},
            )
            db.execute(
                text("DELETE FROM work_items WHERE id = ANY(:ids)"), {"ids": ids}
            )
            db.commit()
        deleted += len(ids)
        if len(ids) < batch_size:
            break

    print(f"    work_items: {deleted} row(s) deleted")
    return deleted


def _purge_conversations(
    session_factory,
    *,
    organization_id,
    days: int,
    apply: bool,
    batch_size: int,
) -> int:
    from sqlalchemy import text

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    with session_factory() as db:
        total = db.execute(
            text(
                """
                SELECT count(*)
                FROM conversations c
                JOIN workspaces w ON w.id = c.workspace_id
                WHERE w.organization_id = :org AND c.created_at < :cutoff
                """
            ),
            {"org": organization_id, "cutoff": cutoff},
        ).scalar_one()

    if not apply:
        print(f"    [DRY RUN] conversations: {total} row(s) older than {days}d")
        return int(total)

    deleted = 0
    while True:
        with session_factory() as db:
            ids = [
                row[0]
                for row in db.execute(
                    text(
                        """
                        SELECT c.id
                        FROM conversations c
                        JOIN workspaces w ON w.id = c.workspace_id
                        WHERE w.organization_id = :org AND c.created_at < :cutoff
                        LIMIT :batch
                        """
                    ),
                    {
                        "org": organization_id,
                        "cutoff": cutoff,
                        "batch": batch_size,
                    },
                ).fetchall()
            ]
            if not ids:
                break
            # conversation_messages.conversation_id is ON DELETE CASCADE.
            db.execute(
                text("DELETE FROM conversations WHERE id = ANY(:ids)"), {"ids": ids}
            )
            db.commit()
        deleted += len(ids)
        if len(ids) < batch_size:
            break

    print(f"    conversations: {deleted} row(s) deleted")
    return deleted


def _sweep_exports(session_factory, *, apply: bool) -> int:
    from app.services.compliance import export_service

    with session_factory() as db:
        count = export_service.expire_stale_exports(db, apply=apply)
        if apply:
            db.commit()

    label = "expired" if apply else "would be expired"
    print(f"  compliance_exports: {count} archive(s) {label}")
    return count


def _reclaim_files(session_factory, *, days: int, apply: bool) -> int:
    """Report uploaded_files tombstoned past the reclamation window.

    Reporting only. The bytes belong to the storage driver and reclaiming them
    is ARCH-07's file reclamation path; duplicating that here would give two
    processes a claim on the same objects.
    """
    from sqlalchemy import text

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    with session_factory() as db:
        count = db.execute(
            text(
                "SELECT count(*) FROM uploaded_files "
                "WHERE deleted_at IS NOT NULL AND deleted_at < :cutoff"
            ),
            {"cutoff": cutoff},
        ).scalar_one()

    print(
        f"  uploaded_files: {count} tombstoned row(s) past {days}d "
        f"(reclamation is ARCH-07's path, not this sweeper's)"
    )
    return int(count)


def main() -> int:
    parser = argparse.ArgumentParser(description="ARCH-20 compliance sweeper")
    parser.add_argument(
        "--all",
        action="store_true",
        help="run the non-destructive sweeps (exports, files, report)",
    )
    parser.add_argument("--exports", action="store_true", help="expire stale archives")
    parser.add_argument("--files", action="store_true", help="report tombstoned files")
    parser.add_argument(
        "--report",
        action="store_true",
        help="print each tenant's retention policy without acting on it",
    )
    parser.add_argument(
        "--purge",
        action="store_true",
        help=(
            "age-based purge of work_items and conversations. Never included "
            "in --all. Requires auto_purge_enabled on the tenant."
        ),
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="actually write. Without this every sweep is a dry run.",
    )
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)
    args = parser.parse_args()

    if not any([args.all, args.exports, args.files, args.report, args.purge]):
        parser.error("Nothing selected. Pass --all, --report, or a specific sweep.")

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    session_factory = _session_factory()

    mode = "APPLY" if args.apply else "DRY RUN"
    print(f"ARCH-20 compliance sweeper — {mode}\n")

    if args.all or args.report:
        print("Retention policies:")
        for policy in _policies(session_factory, require_auto_purge=False):
            print(
                f"  {policy['slug']}: work_items="
                f"{policy['work_item_retention_days']}d "
                f"conversations={policy['conversation_retention_days']}d "
                f"audit={policy['audit_retention_days']}d "
                f"auto_purge={policy['auto_purge_enabled']}"
            )
        print()

    if args.all or args.exports:
        _sweep_exports(session_factory, apply=args.apply)

    if args.all or args.files:
        from app.core.config import settings

        _reclaim_files(
            session_factory,
            days=int(getattr(settings, "FILE_RECLAMATION_DAYS", 30)),
            apply=args.apply,
        )

    if args.purge:
        print("\nAge-based purge (auto_purge_enabled tenants only):")
        eligible = _policies(session_factory, require_auto_purge=True)
        if not eligible:
            print("  no tenant has opted in")
        for policy in eligible:
            print(f"  {policy['slug']}:")
            if policy["work_item_retention_days"]:
                _purge_work_items(
                    session_factory,
                    organization_id=policy["organization_id"],
                    days=policy["work_item_retention_days"],
                    apply=args.apply,
                    batch_size=args.batch_size,
                )
            if policy["conversation_retention_days"]:
                _purge_conversations(
                    session_factory,
                    organization_id=policy["organization_id"],
                    days=policy["conversation_retention_days"],
                    apply=args.apply,
                    batch_size=args.batch_size,
                )
            if policy["audit_retention_days"]:
                print(
                    f"    audit_logs: {policy['audit_retention_days']}d policy "
                    f"recorded; deletion is the ARCH-07 sweeper role's job "
                    f"(400-day floor is enforced by trigger, not by this script)"
                )

    if not args.apply:
        print("\nNothing was written. Re-run with --apply to act.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
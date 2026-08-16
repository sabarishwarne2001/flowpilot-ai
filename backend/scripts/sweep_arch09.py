#!/usr/bin/env python
"""ARCH-09 retention sweeper — outbox events, deliveries, and attempt history."""

from __future__ import annotations

import argparse
import logging
import pathlib
import sys
from datetime import datetime, timedelta, timezone

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

logger = logging.getLogger("sweep_arch09")

DEFAULT_OUTBOX_PUBLISHED_DAYS = 7
DEFAULT_DELIVERY_DELIVERED_DAYS = 30
DEFAULT_DELIVERY_DEAD_DAYS = 90
DEFAULT_BATCH_SIZE = 5_000


def _sweep(
    session_factory,
    *,
    label: str,
    table: str,
    predicate: str,
    params: dict,
    apply: bool,
    batch_size: int,
) -> int:
    from sqlalchemy import text

    with session_factory() as db:
        candidate_count = db.execute(
            text(f"SELECT count(*) FROM {table} WHERE {predicate}"), params
        ).scalar_one()

    if not apply:
        print(f"  [DRY RUN] {label}: {candidate_count} row(s) would be deleted")
        return candidate_count

    deleted = 0
    while True:
        with session_factory() as db:
            rows = db.execute(
                text(
                    f"DELETE FROM {table} WHERE ctid IN ("
                    f"  SELECT ctid FROM {table} WHERE {predicate} LIMIT :batch"
                    f") RETURNING 1"
                ),
                {**params, "batch": batch_size},
            ).fetchall()
            db.commit()
        deleted += len(rows)
        if len(rows) < batch_size:
            break

    print(f"  [APPLIED] {label}: {deleted} row(s) deleted")
    logger.info("sweep.completed", extra={"label": label, "deleted": deleted})
    return deleted


def main() -> int:
    parser = argparse.ArgumentParser(description="ARCH-09 retention sweeper")
    parser.add_argument("--all", action="store_true", help="run every sweep")
    parser.add_argument("--outbox", action="store_true")
    parser.add_argument("--deliveries", action="store_true")
    parser.add_argument("--attempts", action="store_true", help="orphan check only")
    parser.add_argument("--apply", action="store_true", help="actually delete")
    parser.add_argument(
        "--outbox-days", type=int, default=DEFAULT_OUTBOX_PUBLISHED_DAYS
    )
    parser.add_argument(
        "--delivered-days", type=int, default=DEFAULT_DELIVERY_DELIVERED_DAYS
    )
    parser.add_argument("--dead-days", type=int, default=DEFAULT_DELIVERY_DEAD_DAYS)
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s"
    )

    if not any([args.all, args.outbox, args.deliveries, args.attempts]):
        parser.error("select at least one of --all/--outbox/--deliveries/--attempts")

    try:
        from app.db.session import SessionLocal
    except Exception as exc:  # noqa: BLE001
        print(f"[SKIP] cannot import session factory: {exc}")
        return 2

    now = datetime.now(timezone.utc)
    mode = "APPLY" if args.apply else "DRY RUN"
    print(f"ARCH-09 retention sweep — {mode} — {now.isoformat()}\n")

    total = 0

    if args.all or args.outbox:
        cutoff = now - timedelta(days=args.outbox_days)
        total += _sweep(
            SessionLocal,
            label=f"outbox_events PUBLISHED older than {args.outbox_days}d",
            table="outbox_events",
            predicate="status = 'PUBLISHED' AND published_at < :cutoff",
            params={"cutoff": cutoff},
            apply=args.apply,
            batch_size=args.batch_size,
        )

    if args.all or args.deliveries:
        cutoff = now - timedelta(days=args.delivered_days)
        total += _sweep(
            SessionLocal,
            label=f"webhook_deliveries DELIVERED older than {args.delivered_days}d",
            table="webhook_deliveries",
            predicate="status = 'DELIVERED' AND delivered_at < :cutoff",
            params={"cutoff": cutoff},
            apply=args.apply,
            batch_size=args.batch_size,
        )

        dead_cutoff = now - timedelta(days=args.dead_days)
        total += _sweep(
            SessionLocal,
            label=f"webhook_deliveries DEAD older than {args.dead_days}d",
            table="webhook_deliveries",
            predicate="status = 'DEAD' AND updated_at < :cutoff",
            params={"cutoff": dead_cutoff},
            apply=args.apply,
            batch_size=args.batch_size,
        )

    if args.all or args.attempts:
        from sqlalchemy import text

        with SessionLocal() as db:
            orphans = db.execute(
                text(
                    "SELECT count(*) FROM webhook_delivery_attempts a "
                    "LEFT JOIN webhook_deliveries d "
                    "  ON d.id = a.webhook_delivery_id "
                    "WHERE d.id IS NULL"
                )
            ).scalar_one()
            retained = db.execute(
                text("SELECT count(*) FROM webhook_delivery_attempts")
            ).scalar_one()

        print(f"  [INFO]    webhook_delivery_attempts retained: {retained}")
        if orphans:
            print(
                f"  [WARN]    {orphans} orphaned attempt row(s) — the "
                "ON DELETE CASCADE from webhook_deliveries is not firing."
            )
        else:
            print(
                "  [OK]      0 orphans — attempts cascade correctly; no "
                "independent sweep needed"
            )

    print(f"\n{'Would delete' if not args.apply else 'Deleted'}: {total} row(s)")
    if not args.apply:
        print("Re-run with --apply to perform the deletion.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
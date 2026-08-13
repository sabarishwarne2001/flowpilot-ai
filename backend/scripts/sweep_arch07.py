#!/usr/bin/env python
"""ARCH-07 Step 11 — consolidated maintenance sweeper (§B.3, §B.7).

Three independent jobs:
  --audit-retention   delete audit_logs older than RETENTION_DAYS (400)
  --reclaim-files     purge soft-deleted uploaded_files + their objects
  --expire-requests   transition lapsed email_change_requests to EXPIRED
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

backend_dir = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(backend_dir))

from app.core.config import settings
from app.core.storage import ObjectNotFoundError, StorageError, get_storage_driver
from app.db.session import SessionLocal

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s"
)
logger = logging.getLogger("sweep_arch07")

RETENTION_DAYS = 400
BATCH_SIZE = 1_000
ADVISORY_LOCK_KEY = 0x0A7C_0711


@dataclass
class JobResult:
    job: str
    dry_run: bool
    examined: int = 0
    affected: int = 0
    failed: int = 0
    skipped_reason: Optional[str] = None
    notes: list[str] = field(default_factory=list)

    def __str__(self) -> str:
        if self.skipped_reason:
            return f"{self.job:20s} SKIPPED — {self.skipped_reason}"
        verb = "would affect" if self.dry_run else "affected"
        return (
            f"{self.job:20s} examined={self.examined:6d} "
            f"{verb}={self.affected:6d} failed={self.failed:4d}"
        )


def _assert_retention_window_matches_trigger(session: Session) -> None:
    definition = session.execute(
        text("SELECT pg_get_functiondef('fn_audit_logs_prevent_mutation'::regproc)")
    ).scalar_one()

    found = re.findall(r"interval\s+'(\d+)\s+days'", definition)
    if not found:
        logger.warning("Could not find retention interval in fn_audit_logs_prevent_mutation definition.")
        return
    windows = {int(value) for value in found}
    if windows and windows != {RETENTION_DAYS}:
        raise RuntimeError(
            f"RETENTION WINDOW MISMATCH — trigger uses {sorted(windows)} days; "
            f"this script uses {RETENTION_DAYS}."
        )


def _audit_sweeper_session() -> Optional[Session]:
    if settings.AUDIT_SWEEPER_DATABASE_URL is None:
        return None
    engine = create_engine(
        settings.AUDIT_SWEEPER_DATABASE_URL.get_secret_value(),
        pool_pre_ping=True,
    )
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)()


def sweep_audit_retention(*, dry_run: bool) -> JobResult:
    result = JobResult(job="audit-retention", dry_run=dry_run)
    session = _audit_sweeper_session()

    if session is None:
        result.skipped_reason = (
            "AUDIT_SWEEPER_DATABASE_URL is not set. Skipping audit deletion."
        )
        logger.warning("audit-retention SKIPPED: %s", result.skipped_reason)
        return result

    try:
        _assert_retention_window_matches_trigger(session)

        result.examined = session.execute(
            text(
                "SELECT count(*) FROM audit_logs "
                "WHERE created_at < now() - make_interval(days => :days)"
            ),
            {"days": RETENTION_DAYS},
        ).scalar_one()

        if dry_run:
            result.affected = result.examined
            return result

        while True:
            deleted = session.execute(
                text(
                    """
                    DELETE FROM audit_logs
                     WHERE id IN (
                           SELECT id FROM audit_logs
                            WHERE created_at < now() - make_interval(days => :days)
                            ORDER BY created_at
                            LIMIT :batch)
                    """
                ),
                {"days": RETENTION_DAYS, "batch": BATCH_SIZE},
            ).rowcount
            session.commit()
            if not deleted:
                break
            result.affected += deleted
        return result

    except Exception as exc:
        session.rollback()
        result.failed = 1
        result.notes.append(str(exc))
        logger.exception("audit-retention failed")
        return result
    finally:
        session.close()


def sweep_file_reclamation(session: Session, *, dry_run: bool) -> JobResult:
    result = JobResult(job="reclaim-files", dry_run=dry_run)
    driver = get_storage_driver()
    days = settings.FILE_RECLAMATION_DAYS

    rows = session.execute(
        text(
            """
            SELECT id, file_path FROM uploaded_files
             WHERE deleted_at IS NOT NULL
               AND deleted_at < now() - make_interval(days => :days)
             ORDER BY deleted_at
            """
        ),
        {"days": days},
    ).all()
    result.examined = len(rows)

    if dry_run:
        result.affected = len(rows)
        return result

    for row in rows:
        try:
            session.execute(
                text("DELETE FROM uploaded_files WHERE id = :row_id"),
                {"row_id": row.id},
            )
            session.commit()
        except Exception:
            session.rollback()
            result.failed += 1
            continue

        try:
            driver.delete(row.file_path)
        except (StorageError, ObjectNotFoundError):
            result.notes.append(f"orphaned object: {row.file_path}")

        result.affected += 1

    return result


def sweep_expired_requests(session: Session, *, dry_run: bool) -> JobResult:
    result = JobResult(job="expire-requests", dry_run=dry_run)

    result.examined = session.execute(
        text(
            "SELECT count(*) FROM email_change_requests "
            "WHERE status = 'PENDING' AND expires_at < now()"
        )
    ).scalar_one()

    if dry_run:
        result.affected = result.examined
        return result

    try:
        result.affected = session.execute(
            text(
                """
                UPDATE email_change_requests
                   SET status = 'EXPIRED'
                 WHERE status = 'PENDING' AND expires_at < now()
                """
            )
        ).rowcount
        session.commit()
    except Exception as exc:
        session.rollback()
        result.failed = 1
        result.notes.append(str(exc))

    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audit-retention", action="store_true")
    parser.add_argument("--reclaim-files", action="store_true")
    parser.add_argument("--expire-requests", action="store_true")
    parser.add_argument("--all", action="store_true", help="Run all three jobs.")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--apply", action="store_true")
    parser.add_argument("--json", action="store_true", help="Emit a JSON summary.")
    args = parser.parse_args()

    jobs = {
        "audit": args.all or args.audit_retention,
        "files": args.all or args.reclaim_files,
        "requests": args.all or args.expire_requests,
    }
    if not any(jobs.values()):
        parser.error("Select at least one job, or --all.")

    dry_run = args.dry_run
    started = datetime.now(timezone.utc)
    results: list[JobResult] = []

    session = SessionLocal()
    try:
        acquired = session.execute(
            text("SELECT pg_try_advisory_lock(:key)"), {"key": ADVISORY_LOCK_KEY}
        ).scalar_one()
        if not acquired:
            logger.error("Another sweep_arch07 run holds the lock. Exiting.")
            return 2

        try:
            if jobs["audit"]:
                results.append(sweep_audit_retention(dry_run=dry_run))
            if jobs["files"]:
                results.append(sweep_file_reclamation(session, dry_run=dry_run))
            if jobs["requests"]:
                results.append(sweep_expired_requests(session, dry_run=dry_run))
        finally:
            session.execute(
                text("SELECT pg_advisory_unlock(:key)"), {"key": ADVISORY_LOCK_KEY}
            )
            session.commit()
    finally:
        session.close()

    elapsed = (datetime.now(timezone.utc) - started).total_seconds()
    failed = sum(result.failed for result in results)

    if args.json:
        print(json.dumps(
            {
                "started_at": started.isoformat(),
                "elapsed_seconds": round(elapsed, 2),
                "dry_run": dry_run,
                "failed_jobs": failed,
                "results": [asdict(result) for result in results],
            },
            indent=2,
        ))
    else:
        print()
        for result in results:
            print(result)
        print(f"\nelapsed: {elapsed:.1f}s")

    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
"""
Invitation lifecycle sweeper for FlowPilot AI (ARCH-04 §B.7).

Two jobs, in this order:

    1. EXPIRE   PENDING invitations past expires_at become EXPIRED, and each
                inviter receives ONE digest of what lapsed — never one message
                per invitation.
    2. PURGE    Terminal invitations older than INVITATION_RETENTION_DAYS are
                deleted.

ORDER MATTERS. Expire, then digest, then purge.

Usage:
    python -m scripts.sweep_invitations
    python -m scripts.sweep_invitations --dry-run
    python -m scripts.sweep_invitations --skip-purge
    python -m scripts.sweep_invitations --send-delay-ms 500

Exit codes:
    0   completed; every digest delivered
    1   failed, or one or more digests could not be delivered
    2   invalid arguments
    3   another run holds the lock — expected, not a fault
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

# Add backend directory root to sys.path so 'app' imports resolve cleanly
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import sqlalchemy as sa
from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from app.core.links import build_organization_invitations_link
from app.db.session import SessionLocal, make_engine
from app.models.organization_invitation import (
    InvitationStatus,
    OrganizationInvitation,
)
from app.services import invitation_mail
from app.services import organization_invitation_service

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] %(name)s - %(message)s",
)
logger = logging.getLogger("scripts.sweep_invitations")

#: PostgreSQL advisory lock key for this job.
LOCK_KEY = 40408

LARGE_RUN_THRESHOLD = 100
DEFAULT_SEND_DELAY_MS = 250


def main() -> int:
    parser = argparse.ArgumentParser(
        description="ARCH-04 §B.7 invitation sweeper."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "Report what would change and what mail would go out, without "
            "committing anything or sending anything."
        ),
    )
    parser.add_argument(
        "--skip-purge",
        action="store_true",
        help="Expire and notify, but do not delete anything.",
    )
    parser.add_argument(
        "--send-delay-ms",
        type=int,
        default=DEFAULT_SEND_DELAY_MS,
        help=(
            "Pause between digest sends, to stay inside the platform relay's "
            f"rate limit. Default {DEFAULT_SEND_DELAY_MS}."
        ),
    )
    args = parser.parse_args()

    if args.send_delay_ms < 0:
        logger.error("--send-delay-ms cannot be negative.")
        return 2

    engine = make_engine(role="sweeper")
    session_factory = sessionmaker(bind=engine)
    exit_code = 0

    try:
        with session_factory() as db:
            acquired = db.execute(
                sa.text("SELECT pg_try_advisory_lock(:key)"),
                {"key": LOCK_KEY},
            ).scalar_one()

            if not acquired:
                logger.info(
                    "SWEEP_SKIPPED | another run holds lock %s", LOCK_KEY
                )
                return 3

            try:
                exit_code = _run(db, args)
            finally:
                db.execute(
                    sa.text("SELECT pg_advisory_unlock(:key)"),
                    {"key": LOCK_KEY},
                )
    finally:
        engine.dispose()

    return exit_code


def _run(db, args) -> int:
    before = _counts(db)
    logger.info(
        "SWEEP_START | pending=%d | total=%d%s",
        before["pending"], before["total"],
        " | DRY RUN" if args.dry_run else "",
    )

    try:
        batches = organization_invitation_service.sweep_expired_invitations(
            db, commit=not args.dry_run
        )
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        logger.exception("SWEEP_EXPIRE_FAILED | %s", exc)
        return 1

    total_lines = sum(len(b.lines) for b in batches.values())
    logger.info(
        "SWEEP_EXPIRED | invitations=%d | inviters=%d",
        total_lines, len(batches),
    )

    if len(batches) > LARGE_RUN_THRESHOLD:
        logger.warning(
            "SWEEP_LARGE_RUN | %d digests queued, above the usual %d.",
            len(batches), LARGE_RUN_THRESHOLD,
        )

    failed_digests = 0

    for index, (inviter_id, batch) in enumerate(batches.items()):
        if args.dry_run:
            logger.info(
                "SWEEP_DRY_RUN_DIGEST | to=%s | lines=%d",
                batch.inviter_email, len(batch.lines),
            )
            continue

        delivered = invitation_mail.send_expiry_digest(
            inviter_email=batch.inviter_email,
            lines=batch.lines,
            invitations_url=build_organization_invitations_link(
                batch.organization_slug
            ),
        )
        if not delivered:
            failed_digests += 1
            logger.error(
                "SWEEP_DIGEST_FAILED | to=%s | lines=%d | NOT RECOVERABLE",
                batch.inviter_email, len(batch.lines),
            )

        if args.send_delay_ms and index < len(batches) - 1:
            time.sleep(args.send_delay_ms / 1000)

    purged = 0
    if not args.skip_purge:
        try:
            purged = organization_invitation_service.purge_old_invitations(
                db, commit=not args.dry_run
            )
        except Exception as exc:  # noqa: BLE001
            db.rollback()
            logger.exception("SWEEP_PURGE_FAILED | %s", exc)
            return 1

    if args.dry_run:
        db.rollback()
        logger.info(
            "SWEEP_DRY_RUN | would expire %d invitation(s) across %d "
            "inviter(s) and purge %d row(s)",
            total_lines, len(batches), purged,
        )
        return 0

    after = _counts(db)
    logger.info(
        "SWEEP_COMPLETE | expired=%d | digests=%d | failed_digests=%d | "
        "purged=%d | pending_remaining=%d",
        total_lines, len(batches) - failed_digests, failed_digests,
        purged, after["pending"],
    )

    return 1 if failed_digests else 0


def _counts(db) -> dict[str, int]:
    return {
        "total": db.execute(
            select(func.count()).select_from(OrganizationInvitation)
        ).scalar_one(),
        "pending": db.execute(
            select(func.count())
            .select_from(OrganizationInvitation)
            .where(OrganizationInvitation.status == InvitationStatus.PENDING)
        ).scalar_one(),
    }


if __name__ == "__main__":
    sys.exit(main())

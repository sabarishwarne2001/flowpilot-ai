"""
Identity table sweeper for FlowPilot AI (ARCH-03 R8).

Deletes expired sessions and expired single-use tokens once they are old enough
that nobody will need to read them.

    sessions      one row per refresh token, several per device per day
    auth_tokens   one row per verification and per reset

Neither is deleted at expiry. A revoked session is the evidence of a reuse
incident, and an investigation that starts a week later needs the chain intact;
a consumed token is the proof that a verification or reset completed, and "this
link was already used" is a materially different answer to a confused user than
"this link never existed". Both are kept for a retention window past expiry and
removed after.

WHY A SCRIPT AND NOT AN IN-PROCESS SCHEDULER
--------------------------------------------
The obvious implementation is a background loop in the FastAPI lifespan. It is
wrong here for a specific reason: the application runs under multiple uvicorn
workers, and a lifespan hook runs once per worker. Four workers means four
concurrent sweeps racing to delete the same rows — mostly harmless, entirely
wasteful, and impossible to reason about when a deletion count looks odd.

A cron entry or a systemd timer runs exactly once, is visible in the process
list, exits with a status code a monitor can watch, and can be run by hand
during an incident. Add one of:

    # crontab, 03:17 daily — an odd minute, so it does not pile up with every
    # other job in the world that runs at 03:00
    17 3 * * *  cd /srv/flowpilot/backend && /srv/flowpilot/.venv/bin/python -m scripts.sweep_identity

    # systemd timer
    OnCalendar=*-*-* 03:17:00

Usage:
    python -m scripts.sweep_identity
    python -m scripts.sweep_identity --dry-run
    python -m scripts.sweep_identity --retain-days 90
"""

from __future__ import annotations

import argparse
import logging
import sys

from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from app.db.session import SessionLocal, make_engine
from app.models.auth_token import AuthToken
from app.models.user_session import UserSession
from app.services import auth_token_service, session_service

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] %(name)s - %(message)s",
)
logger = logging.getLogger("scripts.sweep_identity")

DEFAULT_RETAIN_DAYS = 30


def main() -> int:
    parser = argparse.ArgumentParser(description="ARCH-03 R8 identity sweeper.")
    parser.add_argument(
        "--retain-days",
        type=int,
        default=DEFAULT_RETAIN_DAYS,
        help=(
            "How long past expiry a row is kept. Rows are deleted only once "
            f"they expired more than this many days ago. Default "
            f"{DEFAULT_RETAIN_DAYS}."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would be deleted without deleting it.",
    )
    args = parser.parse_args()

    if args.retain_days < 1:
        # A retention of zero would delete a session in the same minute it
        # expired, which is exactly when someone is most likely to be asking
        # why they were signed out.
        logger.error("--retain-days must be at least 1.")
        return 2

    engine = make_engine(role="sweeper")
    session_factory = sessionmaker(bind=engine)

    with session_factory() as db:
        before = _counts(db)
        logger.info(
            "SWEEP_START | sessions=%d | auth_tokens=%d | retain_days=%d%s",
            before["sessions"],
            before["auth_tokens"],
            args.retain_days,
            " | DRY RUN" if args.dry_run else "",
        )

        try:
            sessions_removed = session_service.sweep_expired_sessions(
                db, retain_days=args.retain_days
            )
            tokens_removed = auth_token_service.sweep_expired_tokens(
                db, retain_days=args.retain_days
            )

            if args.dry_run:
                # The sweep functions flush but never commit, so a rollback
                # discards the whole thing. This is what makes --dry-run an
                # exact rehearsal rather than an approximation of one.
                db.rollback()
                logger.info(
                    "SWEEP_DRY_RUN | would delete %d sessions and %d tokens",
                    sessions_removed,
                    tokens_removed,
                )
            else:
                db.commit()
                logger.info(
                    "SWEEP_COMPLETE | deleted %d sessions and %d tokens",
                    sessions_removed,
                    tokens_removed,
                )
        except Exception as exc:  # noqa: BLE001 — reported via exit status
            db.rollback()
            logger.exception("SWEEP_FAILED | %s", exc)
            return 1

        after = _counts(db)
        logger.info(
            "SWEEP_END | sessions=%d | auth_tokens=%d",
            after["sessions"],
            after["auth_tokens"],
        )

    engine.dispose()
    return 0


def _counts(db) -> dict[str, int]:
    return {
        "sessions": db.execute(
            select(func.count()).select_from(UserSession)
        ).scalar_one(),
        "auth_tokens": db.execute(
            select(func.count()).select_from(AuthToken)
        ).scalar_one(),
    }


if __name__ == "__main__":
    sys.exit(main())
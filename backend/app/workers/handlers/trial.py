"""`trial.reminders` — the two free-trial emails (owner decision N-052; LIGHT profile, hourly).

* Two days before an account's trial ends: "Your trial ends on <date>", once.
* When it has ended: "Your trial has ended", once, and only within three days of the
  end (an account whose paid plan lapses to Free years later is asked to subscribe
  in the product, not told its trial "has ended").

The trial belongs to the owner ACCOUNT (`users.trial_ends_at`), so the email goes to
the account, naming every organization of theirs still on the trial. An account all
of whose organizations subscribed gets nothing. Each email is recorded on the account
(`trial_reminder_sent_at`, `trial_ended_notice_sent_at`) under a row lock, so two
workers never both send one; an email the relay did not accept is tried again on the
next run, inside its window.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy import select

from app.core.config import settings

logger = logging.getLogger("app.workers.handlers.trial")

JOB_TYPE = "trial.reminders"
REMIND_BEFORE = timedelta(days=2)
ENDED_NOTICE_WINDOW = timedelta(days=3)


def _plans_link(organization_slug: str) -> str:
    return f"{settings.FRONTEND_URL.rstrip('/')}/organizations/{organization_slug}/billing"


def _organizations_on_trial(db: Any, user_id: Any, *, want: str, now: datetime) -> list[Any]:
    from app.models.organization import MembershipStatus, Organization, OrganizationMember, OrganizationRole, OrganizationStatus
    from app.services import trial_service

    owned = db.execute(
        select(Organization)
        .join(OrganizationMember, OrganizationMember.organization_id == Organization.id)
        .where(
            OrganizationMember.user_id == user_id,
            OrganizationMember.role == OrganizationRole.OWNER,
            OrganizationMember.status == MembershipStatus.ACTIVE,
            Organization.status == OrganizationStatus.ACTIVE,
        )
        .order_by(Organization.created_at)
    ).scalars().all()
    on_trial = []
    for org in owned:
        trial = trial_service.status(db, organization_id=org.id, now=now)
        # Campaign session 4 (F-342): an organization whose paid plan lapsed is not told its trial ended.
        if trial.state == want and not trial.lapsed:
            on_trial.append(org)
    return on_trial


def _send(*, kind: str, recipient: str, organizations: list[Any], ends_at: datetime) -> bool:
    from app.core.platform_email import PlatformEmailNotConfigured, send_platform_email
    from app.templates.emails.trial_notice import render_trial_notice

    subject, html_body, text_body = render_trial_notice(
        kind=kind,
        organization_names=[org.name for org in organizations],
        ends_at=ends_at,
        plans_link=_plans_link(organizations[0].slug),
        brand_name=settings.PROJECT_NAME,
    )
    try:
        delivered, detail = send_platform_email(
            recipient=recipient, subject=subject, html_body=html_body, text_body=text_body
        )
    except PlatformEmailNotConfigured as exc:
        logger.warning("trial.notice_unconfigured", extra={"kind": kind, "error": str(exc)})
        return False
    if not delivered:
        logger.warning("trial.notice_not_delivered", extra={"kind": kind, "detail": str(detail)})
    return bool(delivered)


def run_reminders(db: Any, *, now: Optional[datetime] = None) -> dict[str, int]:
    """Send what is due; returns how many of each went out. Commits per account."""
    from app.models.user import User

    moment = now or datetime.now(timezone.utc)
    sent = {"ending": 0, "ended": 0}

    due_ending = db.execute(
        select(User.id).where(
            User.trial_ends_at.is_not(None),
            User.trial_ends_at > moment,
            User.trial_ends_at <= moment + REMIND_BEFORE,
            User.trial_reminder_sent_at.is_(None),
            User.is_active.is_(True),
        )
    ).scalars().all()
    for user_id in due_ending:
        user = db.execute(select(User).where(User.id == user_id).with_for_update(skip_locked=True)).scalar_one_or_none()
        if user is None or user.trial_reminder_sent_at is not None:
            db.rollback()
            continue
        organizations = _organizations_on_trial(db, user.id, want="ACTIVE", now=moment)
        if not organizations:
            user.trial_reminder_sent_at = moment  # nothing on trial any more: nothing to say
        elif _send(kind="ENDING", recipient=user.email, organizations=organizations, ends_at=user.trial_ends_at):
            user.trial_reminder_sent_at = moment
            sent["ending"] += 1
        db.commit()  # an undelivered email is retried on the next run, inside its window

    due_ended = db.execute(
        select(User.id).where(
            User.trial_ends_at.is_not(None),
            User.trial_ends_at <= moment,
            User.trial_ends_at > moment - ENDED_NOTICE_WINDOW,
            User.trial_ended_notice_sent_at.is_(None),
            User.is_active.is_(True),
        )
    ).scalars().all()
    for user_id in due_ended:
        user = db.execute(select(User).where(User.id == user_id).with_for_update(skip_locked=True)).scalar_one_or_none()
        if user is None or user.trial_ended_notice_sent_at is not None:
            db.rollback()
            continue
        organizations = _organizations_on_trial(db, user.id, want="ENDED", now=moment)
        if not organizations:
            user.trial_ended_notice_sent_at = moment
        elif _send(kind="ENDED", recipient=user.email, organizations=organizations, ends_at=user.trial_ends_at):
            user.trial_ended_notice_sent_at = moment
            sent["ended"] += 1
        db.commit()
    return sent


def handle_reminders(payload: dict[str, Any]) -> dict[str, Any]:
    from app.db import session as session_module

    with session_module.SessionLocal() as db:
        sent = run_reminders(db)
    if sent["ending"] or sent["ended"]:
        logger.info("trial.notices_sent", extra=sent)
    return {"sent": sent}


__all__ = ["JOB_TYPE", "handle_reminders", "run_reminders"]

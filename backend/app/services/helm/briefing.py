"""The Monday briefing: this week's plan, the risks, and the value held, at 09:00 local time.

"Local" is the book's workspace's time zone (an all-workspaces book uses its organization's oldest
workspace): organizations have no clock of their own, workspaces do (N-064). Sent to the
organization's owners and admins who have not turned off the "Helm Monday briefing" notification
category, once per book per ISO week (recorded in the book's settings before sending, so two
workers or a retried job never send it twice). Never for the sample company.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.helm import HelmAction, HelmBook, HelmPlan, HelmSettings
from app.models.workspace import Workspace
from app.services.helm import money
from app.services.helm import vocabulary as v

logger = logging.getLogger("app.services.helm.briefing")

SEND_HOUR = 9


def _zone(db: Session, book: HelmBook) -> ZoneInfo:
    workspace = db.get(Workspace, book.workspace_id) if book.workspace_id else db.execute(
        select(Workspace).where(Workspace.organization_id == book.organization_id)
        .order_by(Workspace.created_at)).scalars().first()
    try:
        return ZoneInfo(workspace.timezone if workspace and workspace.timezone else "UTC")
    except Exception:  # noqa: BLE001 - an unknown zone falls back to UTC rather than never sending
        return ZoneInfo("UTC")


def due(db: Session, book: HelmBook, settings: HelmSettings, now: datetime) -> Optional[str]:
    """The ISO week ("2026-W43") this book's briefing is due for now, or None."""
    local = now.astimezone(_zone(db, book))
    if local.isoweekday() != 1 or local.hour != SEND_HOUR:
        return None
    week = f"{local.isocalendar().year}-W{local.isocalendar().week:02d}"
    if (settings.preferences or {}).get("briefing_sent_week") == week:
        return None
    return week


def content(db: Session, book: HelmBook) -> dict[str, Any]:
    from app.services.helm import value

    plan = db.execute(select(HelmPlan).where(HelmPlan.book_id == book.id, HelmPlan.status == v.PLAN_CURRENT)
                      ).scalar_one_or_none()
    summary = (plan.summary or {}) if plan else {}
    week_end = datetime.now(timezone.utc).date() + timedelta(days=7)
    actions = db.execute(select(HelmAction).where(
        HelmAction.book_id == book.id, HelmAction.state.in_((v.ACTION_PROPOSED, v.ACTION_APPROVED)))).scalars().all()
    plan_lines = []
    for action in sorted(actions, key=lambda a: (a.run_date or week_end, a.title))[:12]:
        if action.run_date and action.run_date > week_end:
            continue
        plan_lines.append(action.title)
    risk_lines = []
    for instrument in summary.get("instruments") or []:
        if instrument.get("state") in ("breached", "drifting"):
            risk_lines.append(f"{instrument.get('title')}: {instrument.get('detail')}")
    if plan and plan.conflict:
        risk_lines.append(plan.conflict.get("headline", ""))
    totals = value.totals(db, book)
    value_line = ", ".join(money.say(amount, currency) for currency, amount in sorted(totals["total"].items())) \
        or "nothing yet (only outcomes backed by evidence count)"
    waiting = sum(1 for a in actions if a.state == v.ACTION_PROPOSED and a.needs_approval)
    return {"plan_lines": plan_lines, "risk_lines": risk_lines[:8], "value_line": value_line, "waiting": waiting}


def send(db: Session, book: HelmBook, *, week: str) -> int:
    from app.core.config import settings as app_settings
    from app.models.organization import MembershipStatus, Organization, OrganizationMember, OrganizationRole
    from app.models.user import User
    from app.services import notification_preferences_service as prefs
    from app.services.helm import hands
    from app.templates.emails import helm as templates

    org = db.get(Organization, book.organization_id)
    data = content(db, book)
    base = app_settings.FRONTEND_URL.rstrip("/")
    cockpit = f"{base}/{org.slug}/helm/cockpit?book={book.id}"
    preferences = f"{base}/organizations/{org.slug}/notifications"
    subject, html, text = templates.render_briefing(
        brand_name=app_settings.PROJECT_NAME, organization_name=org.legal_name or org.name, book_name=book.name,
        week_of=week, plan_lines=data["plan_lines"], risk_lines=data["risk_lines"], value_line=data["value_line"],
        waiting=data["waiting"], cockpit_url=cockpit, preferences_url=preferences)
    sent = 0
    for user_id, email in db.execute(
        select(OrganizationMember.user_id, User.email).join(User, User.id == OrganizationMember.user_id)
        .where(OrganizationMember.organization_id == book.organization_id,
               OrganizationMember.status == MembershipStatus.ACTIVE,
               OrganizationMember.role.in_((OrganizationRole.OWNER, OrganizationRole.ADMIN)))
    ).all():
        if not prefs.email_enabled(db, user_id=user_id, category_key=prefs.HELM_BRIEFING):
            continue
        try:
            hands.send_mail(db, organization_id=book.organization_id, workspace_id=book.workspace_id, to=email,
                            subject=subject, html_body=html, text_body=text)
            sent += 1
        except Exception:  # noqa: BLE001 - one address failing must not stop the others
            logger.warning("helm.briefing_send_failed", extra={"book_id": str(book.id)}, exc_info=True)
    return sent


def sweep(*, now: Optional[datetime] = None) -> dict[str, Any]:
    from app.db.session import SessionLocal
    from app.services.helm import gate
    from app.workers.tenant_gate import inactive_reason, unpaid_reason

    now = now or datetime.now(timezone.utc)
    with SessionLocal() as db:
        books = db.execute(select(HelmBook.id).where(HelmBook.status == v.BOOK_ACTIVE,
                                                     HelmBook.kind != v.BOOK_SAMPLE)).scalars().all()
    sent_books, emails = 0, 0
    for book_id in books:
        with SessionLocal() as db:
            book = db.get(HelmBook, book_id)
            settings = db.get(HelmSettings, book_id)
            if book is None or settings is None or not settings.briefing_enabled:
                continue
            if inactive_reason(db, organization_id=book.organization_id) or \
                    unpaid_reason(db, organization_id=book.organization_id) or \
                    not gate.helm_held(db, book.organization_id):
                continue
            week = due(db, book, settings, now)
            if week is None:
                continue
            locked = db.execute(select(HelmSettings).where(HelmSettings.book_id == book_id)
                                .with_for_update(skip_locked=True)).scalar_one_or_none()
            if locked is None or (locked.preferences or {}).get("briefing_sent_week") == week:
                continue
            locked.preferences = {**(locked.preferences or {}), "briefing_sent_week": week}
            db.flush()
            emails += send(db, book, week=week)
            db.commit()
            sent_books += 1
    return {"books": sent_books, "emails": emails}


__all__ = ["content", "due", "send", "sweep"]

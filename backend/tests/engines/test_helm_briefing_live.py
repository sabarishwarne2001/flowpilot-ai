"""The Monday briefing on real Postgres (brief §10.3): 09:00 on Monday in the book's own time zone,
to the organization's owners and admins who have not turned it off, once a week, never for the
sample company, and its links lead to pages that exist."""

from __future__ import annotations

from datetime import datetime, timezone

from app.models.helm import HelmBook, HelmSettings
from app.services import notification_preferences_service as prefs
from app.services.helm import briefing, hands
from app.services.helm import vocabulary as v
from tests.security.plans import put_on_plan

# Monday 19 October 2026, 09:15 in Kolkata (03:45 UTC); and the same Monday at 10:15 there.
MONDAY_9_IST = datetime(2026, 10, 19, 3, 45, tzinfo=timezone.utc)
MONDAY_10_IST = datetime(2026, 10, 19, 4, 45, tzinfo=timezone.utc)
SUNDAY_9_IST = datetime(2026, 10, 18, 3, 45, tzinfo=timezone.utc)


def _book(db_session, tenant, *, kind: str = v.BOOK_WORKSPACE) -> HelmBook:
    put_on_plan(db_session, tenant.organization, "business")
    tenant.workspace.timezone = "Asia/Kolkata"
    book = HelmBook(organization_id=tenant.organization.id, workspace_id=tenant.workspace.id, kind=kind,
                    name="Engineering", status=v.BOOK_ACTIVE)
    db_session.add(book)
    db_session.flush()
    db_session.add(HelmSettings(book_id=book.id, organization_id=book.organization_id, briefing_enabled=True,
                                identities=[{"name": "Acme Inc.", "tax_id": None}]))
    db_session.commit()
    return book


def _outbox(monkeypatch) -> list[dict]:
    sent: list[dict] = []
    monkeypatch.setattr(hands, "send_mail", lambda db, **mail: sent.append(mail) or {"sent": True})
    return sent


def test_the_briefing_is_due_at_nine_on_monday_in_the_books_own_time_zone(db_session, tenant) -> None:
    book = _book(db_session, tenant)
    settings = db_session.get(HelmSettings, book.id)
    assert briefing.due(db_session, book, settings, MONDAY_9_IST) == "2026-W43"
    assert briefing.due(db_session, book, settings, MONDAY_10_IST) is None
    assert briefing.due(db_session, book, settings, SUNDAY_9_IST) is None


def test_owners_and_admins_get_it_once_a_week_unless_they_turned_it_off(db_session, tenant, test_sessions,
                                                                       monkeypatch) -> None:
    book = _book(db_session, tenant)
    prefs.set_email(db_session, user_id=tenant.org_admin.user.id, category_key=prefs.HELM_BRIEFING, enabled=False)
    db_session.commit()
    outbox = _outbox(monkeypatch)

    assert briefing.sweep(now=MONDAY_9_IST)["emails"] == 1
    assert [mail["to"] for mail in outbox] == [tenant.owner.user.email]  # not the admin who opted out, nor members
    mail = outbox[0]
    assert mail["subject"] == "Helm briefing for Engineering, week of 19 Oct 2026"
    # The links lead to pages that exist: the cockpit is /:org/helm, preferences are the person's own.
    cockpit = f"/{tenant.organization.slug}/helm?book={book.id}"
    for body in (mail["text_body"], mail["html_body"]):
        assert cockpit in body
        assert "/helm/cockpit" not in body
        assert "/settings?section=notifications" in body

    assert briefing.sweep(now=MONDAY_9_IST)["emails"] == 0, "never twice in one week"


def test_never_for_the_sample_company(db_session, tenant, test_sessions, monkeypatch) -> None:
    _book(db_session, tenant, kind=v.BOOK_SAMPLE)
    outbox = _outbox(monkeypatch)
    assert briefing.sweep(now=MONDAY_9_IST) == {"books": 0, "emails": 0}
    assert outbox == []

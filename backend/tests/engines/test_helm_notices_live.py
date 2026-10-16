"""Helm's notices reach owners and admins (found by the 10,000-document performance run).

Helm passed a button label ("Open Helm", "Open the Helm cockpit") where the notification service
takes a button key, so the service raised KeyError as soon as it built the email: a newly breached
mandate and a hold were logged as "notify failed" and never reached anyone, and a mandate's notify
action (the review backlog's) failed outright. Each notice now arrives, in the bell and by email,
with a button to Helm's cockpit.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

from sqlalchemy import select

from app.models.helm import HelmAction, HelmBook, HelmPlan
from app.models.notification import Notification
from app.services.helm import hands, plan_store
from app.services.helm import vocabulary as v
from app.services.helm.mandates import base


def _book(db, tenant) -> HelmBook:
    book = HelmBook(organization_id=tenant.organization.id, workspace_id=tenant.workspace.id, kind=v.BOOK_WORKSPACE,
                    name="Engineering", status=v.BOOK_ACTIVE)
    db.add(book)
    db.flush()
    return book


def _action(book: HelmBook, kind: str, **kw) -> HelmAction:
    return HelmAction(id=uuid.uuid4(), organization_id=book.organization_id, book_id=book.id, kind=kind,
                      idempotency_key=f"helm:test:{uuid.uuid4().hex}", state=v.ACTION_EXECUTING,
                      autonomy=v.AUTONOMY_PROPOSE, title=kw.get("title", "Review items waiting"),
                      explanation=kw.get("explanation", "Escalate or reassign in the review hub."),
                      amount_micros=kw.get("amount_micros"), currency=kw.get("currency"), params={}, approvals=[],
                      required_approvals=1, guard={}, result={}, needs_approval=True, guard_passed=True)


def _received(db, tenant, title: str) -> list[Notification]:
    return db.execute(select(Notification).where(Notification.user_id == tenant.owner.user.id,
                                                 Notification.title.like(f"{title}%"))).scalars().all()


def test_a_notify_action_reaches_owners(db_session, tenant):
    book = _book(db_session, tenant)
    out = hands.notify(db_session, action=_action(book, v.ACTION_NOTIFY), book=book)
    assert out["notified"] >= 2  # the owner and the admin
    assert _received(db_session, tenant, "Review items waiting")


def test_a_hold_reaches_owners(db_session, tenant):
    from app.services.helm import executor

    book = _book(db_session, tenant)
    executor._notify_hold(db_session, _action(book, v.ACTION_HOLD_PAYMENT, title="Hold KP-1182",
                                              amount_micros=50_000_000_000, currency="INR"), book)
    assert _received(db_session, tenant, "Helm held")


def test_a_newly_breached_mandate_reaches_owners(db_session, tenant):
    book = _book(db_session, tenant)
    plan = HelmPlan(id=uuid.uuid4(), organization_id=book.organization_id, book_id=book.id)
    mandate = SimpleNamespace(id="m1", title="Keep cash above ₹10 lakh")
    composition = SimpleNamespace(mandates=[mandate], conflict=None, drifts={
        "m1": base.Drift(base.BREACHED, "₹2 lakh below", "Cash falls below the floor on 20 Oct 2026.")})
    plan_store._notify(db_session, book, plan, composition, previous={})
    [notice] = _received(db_session, tenant, "Helm: Engineering needs attention")
    assert "Keep cash above" in notice.message

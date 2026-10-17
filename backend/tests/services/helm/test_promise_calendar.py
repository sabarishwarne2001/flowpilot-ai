"""The cockpit's Promise calendar (Helm visual session): money due per day for the coming weeks, per
direction and currency, from open, partly kept and broken payment promises. Amounts come only from
workspaces the viewer can read; promises elsewhere are counted per day, never summed. The Promise
ledger filters to one due day, which is where a calendar day opens."""

from __future__ import annotations

import uuid
from datetime import date, timedelta

from app.models.helm import HelmBook, HelmPromise
from app.models.workspace import Workspace, WorkspaceStatus
from app.services.helm import service
from app.services.helm import vocabulary as v

START = date(2026, 10, 19)


def _promise(db, organization_id, workspace_id, *, kind, direction, due, micros, status=v.STATUS_OPEN,
             currency="INR"):
    db.add(HelmPromise(organization_id=organization_id, workspace_id=workspace_id, source_key=uuid.uuid4().hex,
                       kind=kind, direction=direction, currency=currency, amount_micros=micros, due_date=due,
                       status=status, confidence=v.CONFIDENCE_EXTRACTED, title="A promise", engine_version="test",
                       content_hash="a" * 64))


def _setup(db_session, tenant):
    finance = Workspace(organization_id=tenant.organization.id, slug=f"finance-{uuid.uuid4().hex[:6]}",
                        workspace_name="Finance", status=WorkspaceStatus.ACTIVE)
    db_session.add(finance)
    book = HelmBook(organization_id=tenant.organization.id, workspace_id=None, kind=v.BOOK_ORGANIZATION,
                    name="Acme", status=v.BOOK_ACTIVE)
    db_session.add(book)
    db_session.flush()
    org, ws = tenant.organization.id, tenant.workspace.id
    pay, owe = v.KIND_PAYMENT_DUE, v.DIRECTION_WE_OWE
    _promise(db_session, org, ws, kind=pay, direction=owe, due=START, micros=1_000_000_000)
    _promise(db_session, org, ws, kind=pay, direction=owe, due=START, micros=250_000_000, status=v.STATUS_PARTIAL)
    _promise(db_session, org, ws, kind=pay, direction=owe, due=START, micros=40_000_000, currency="USD")
    _promise(db_session, org, ws, kind=pay, direction=owe, due=START + timedelta(days=3), micros=5_000_000,
             status=v.STATUS_KEPT)                                                        # kept: not due
    _promise(db_session, org, ws, kind=pay, direction=owe, due=START - timedelta(days=1), micros=7_000_000)
    _promise(db_session, org, ws, kind=pay, direction=owe, due=START + timedelta(days=84), micros=9_000_000)
    _promise(db_session, org, ws, kind=v.KIND_PAYMENT_EXPECTED, direction=v.DIRECTION_OWED_TO_US,
             due=START + timedelta(days=2), micros=3_000_000_000)
    _promise(db_session, org, ws, kind=v.KIND_PRICE_AGREED, direction=owe, due=START, micros=99_000_000)
    _promise(db_session, org, finance.id, kind=pay, direction=owe, due=START, micros=8_000_000_000)
    _promise(db_session, org, finance.id, kind=pay, direction=owe, due=START, micros=8_000_000_000)
    db_session.flush()
    viewer = service.Viewer(organization_id=org, user_id=uuid.uuid4(), role="OWNER", readable=frozenset({ws}))
    return book, viewer


def test_money_due_per_day_from_readable_workspaces_only(db_session, tenant) -> None:
    book, viewer = _setup(db_session, tenant)

    calendar = service.promise_calendar(db_session, viewer, book, start=START, days=84)

    assert calendar["start"] == START.isoformat() and calendar["days"] == 84
    assert calendar["entries"] == [
        {"direction": "OWED_TO_US", "on": "2026-10-21", "currency": "INR", "amount_micros": 3_000_000_000, "count": 1},
        {"direction": "WE_OWE", "on": "2026-10-19", "currency": "INR", "amount_micros": 1_250_000_000, "count": 2},
        {"direction": "WE_OWE", "on": "2026-10-19", "currency": "USD", "amount_micros": 40_000_000, "count": 1},
    ]
    # Finance's two promises are there, but nothing says how much they are.
    assert calendar["hidden"] == [{"direction": "WE_OWE", "on": "2026-10-19", "count": 2}]


def test_the_ledger_filters_to_one_due_day(db_session, tenant) -> None:
    book, viewer = _setup(db_session, tenant)

    day = service.ledger_view(db_session, viewer, book, direction=v.DIRECTION_WE_OWE, due_on=START)

    assert day["total"] == 6
    assert {p["due_date"] for p in day["promises"]} == {START.isoformat()}

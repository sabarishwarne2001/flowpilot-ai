"""Helm's outgoing webhooks (Enterprise), on real Postgres.

* Business sends none: a plan writes no Helm event, and the webhook destination says why in words.
* Enterprise writes an event only when an endpoint listens for it: an endpoint subscribed to other
  events gets nothing from Helm, and no outbox row is written for nobody.
* A `webhook` action with no endpoint listening is BLOCKED by the Guard's destination check, which
  says what to do, instead of running and sending nowhere.
"""

from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import date, datetime, timezone
from typing import Optional

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.helm import HelmAction, HelmBook, HelmSettings
from app.models.outbox_event import OutboxEvent
from app.services import webhook_dispatch, webhook_service
from app.services.helm import executor, hands, plan_store
from app.services.helm import vocabulary as v
from tests.conftest import Fixture
from tests.engines.test_webhooks_live import _deliver, _Receiver, _verify
from tests.security.plans import put_on_plan

URL = "https://hooks.acme.example.com/flowpilot"


def _book(db: Session, tenant: Fixture, plan: str) -> HelmBook:
    put_on_plan(db, tenant.organization, plan)
    book = HelmBook(organization_id=tenant.organization.id, workspace_id=tenant.workspace.id, kind=v.BOOK_WORKSPACE,
                    name="Engineering", status=v.BOOK_ACTIVE)
    db.add(book)
    db.flush()
    db.add(HelmSettings(book_id=book.id, organization_id=book.organization_id,
                        identities=[{"name": "Meridian Wholesale", "tax_id": None}]))
    db.commit()
    return book


def _endpoint(db: Session, tenant: Fixture, events: list[str]):
    endpoint, _secret = webhook_service.register_endpoint(db, organization_id=tenant.organization.id, url=URL,
                                                          event_types=events, created_by_user_id=tenant.owner.user.id)
    db.commit()
    return endpoint


def _helm_events(db: Session, tenant: Fixture) -> list[OutboxEvent]:
    return list(db.execute(select(OutboxEvent).where(OutboxEvent.organization_id == tenant.organization.id,
                                                     OutboxEvent.event_type.like("helm.%"))).scalars())


def test_business_sends_no_helm_webhooks(db_session: Session, tenant: Fixture) -> None:
    book = _book(db_session, tenant, "business")
    assert plan_store.run(db_session, book, force=True) is not None
    db_session.commit()
    assert _helm_events(db_session, tenant) == []
    allowed, words = hands.webhook_destination(db_session, organization_id=tenant.organization.id)
    assert allowed is False and "Enterprise" in words, words


def test_enterprise_writes_no_event_nobody_listens_for(db_session: Session, tenant: Fixture) -> None:
    book = _book(db_session, tenant, "enterprise")
    _endpoint(db_session, tenant, ["document.completed"])
    assert plan_store.run(db_session, book, force=True) is not None
    db_session.commit()
    assert _helm_events(db_session, tenant) == []
    allowed, words = hands.webhook_destination(db_session, organization_id=tenant.organization.id)
    assert allowed is False and "No webhook endpoint listens for Helm's events" in words, words


def test_a_webhook_action_with_no_listener_is_blocked_in_words(db_session: Session, tenant: Fixture) -> None:
    from app.workers.handlers import register_all

    register_all(replace=True)  # a blocked action asks for a re-plan (a `helm.plan` job)
    book = _book(db_session, tenant, "enterprise")
    action = HelmAction(organization_id=book.organization_id, book_id=book.id, workspace_id=book.workspace_id,
                        kind=v.ACTION_WEBHOOK, idempotency_key=f"helm:test:{uuid.uuid4().hex}",
                        state=v.ACTION_APPROVED, autonomy=v.AUTONOMY_PROPOSE, needs_approval=True, guard_passed=True,
                        title="Tell the ERP", explanation="A test webhook.", params={}, undoable=False,
                        approvals=[{"role": "OWNER", "at": datetime.now(timezone.utc).isoformat()}])
    db_session.add(action)
    db_session.commit()
    executor.execute(db_session, action_id=action.id)
    db_session.commit()
    db_session.refresh(action)
    assert action.state == v.ACTION_BLOCKED, action.result
    assert "destination" in action.result["failed"], action.result
    [check] = [c for c in action.guard["checks"] if c["id"] == "destination"]
    assert "No webhook endpoint listens for Helm's helm.mandate.breached event" in check["detail"], check
    assert _helm_events(db_session, tenant) == []


# ---------------------------------------------------------------------------
# With the vocabulary (migration h1a4_helm_api_vocabulary): endpoints subscribe to Helm's events
# ---------------------------------------------------------------------------

API = "/api/v1"
M = 1_000_000
#: Text the book's documents and settings hold; none of it may leave in a webhook.
NEVER_SENT = ("Meridian Wholesale", "Keep cash above", "below the floor")


@pytest.fixture
def receiver(monkeypatch) -> _Receiver:
    stub = _Receiver()
    monkeypatch.setattr(webhook_dispatch.egress, "http_client", lambda *_a, **_kw: stub)
    return stub


def _subscribe(db: Session, tenant: Fixture, events: list[str]) -> str:
    _endpoint_row, secret = webhook_service.register_endpoint(db, organization_id=tenant.organization.id, url=URL,
                                                              event_types=events,
                                                              created_by_user_id=tenant.owner.user.id)
    db.commit()
    return secret


def _received(secret: str, receiver: _Receiver, since: int = 0) -> list[dict]:
    envelopes = []
    for request in receiver.requests[since:]:
        body = request["body"].decode()
        for text in NEVER_SENT:
            assert text not in body, f"{text!r} left in a webhook: {body}"
        envelopes.append(_verify(secret, request))
    return envelopes


def _helm_url(tenant: Fixture, path: str) -> str:
    return f"{API}/organizations/{tenant.organization.id}/helm{path}"


def _breached_book(client, tenant: Fixture) -> str:
    """A book whose cash (₹50,000) is under its mandate's floor (₹1 lakh): the instrument is breached."""
    response = client.post(_helm_url(tenant, "/books"), json={"workspace_id": str(tenant.workspace.id)},
                           headers=tenant.owner.headers)
    assert response.status_code == 201, response.text
    book = response.json()["id"]
    response = client.patch(_helm_url(tenant, f"/books/{book}/settings"),
                            json={"identities": [{"name": "Meridian Wholesale", "tax_id": "27AAACM1234F1Z5"}]},
                            headers=tenant.owner.headers)
    assert response.status_code == 200, response.text
    response = client.post(_helm_url(tenant, f"/books/{book}/cash-positions"),
                           json={"currency": "INR", "amount": "50000", "as_of": date.today().isoformat()},
                           headers=tenant.owner.headers)
    assert response.status_code == 201, response.text
    response = client.post(_helm_url(tenant, f"/books/{book}/mandates"),
                           json={"template": "keep_cash_above", "activate": True, "title": "Keep cash above ₹1 lakh",
                                 "params": {"floor": {"amount_micros": 100_000 * M, "currency": "INR"}}},
                           headers=tenant.owner.headers)
    assert response.status_code == 201, response.text
    return book


def test_a_plan_sends_plan_ready_and_a_new_breach_to_a_subscribed_endpoint(
        client, db_session: Session, tenant: Fixture, test_sessions, receiver: _Receiver) -> None:
    put_on_plan(db_session, tenant.organization, "enterprise")
    db_session.commit()
    secret = _subscribe(db_session, tenant, [v.WEBHOOK_PLAN_READY, v.WEBHOOK_MANDATE_BREACHED])
    book = _breached_book(client, tenant)
    planned = client.post(_helm_url(tenant, f"/books/{book}/plan"), headers=tenant.owner.headers)
    assert planned.status_code == 200, planned.text
    plan = planned.json()["plan"]
    _deliver()
    envelopes = _received(secret, receiver)
    assert sorted(e["type"] for e in envelopes) == [v.WEBHOOK_MANDATE_BREACHED, v.WEBHOOK_PLAN_READY], envelopes
    ready = next(e["data"] for e in envelopes if e["type"] == v.WEBHOOK_PLAN_READY)
    assert ready["plan_id"] == plan["id"] and ready["book_id"] == book and ready["version"] == plan["version"]
    assert set(ready["counts"]) == {"actions", "payments", "needs_approval", "blocked"}
    breach = next(e["data"] for e in envelopes if e["type"] == v.WEBHOOK_MANDATE_BREACHED)
    assert breach["template"] == "keep_cash_above" and breach["state"] == "breached", breach
    assert breach["figure_value"] == 50_000.0 and breach["previous_state"] is None, breach
    assert breach["plan_id"] == plan["id"], breach

    # Planned again with the instrument still breached: a new plan is ready, the breach is not new.
    seen = len(receiver.requests)
    assert client.post(_helm_url(tenant, f"/books/{book}/plan"), headers=tenant.owner.headers).status_code == 200
    _deliver()
    assert [e["type"] for e in _received(secret, receiver, since=seen)] == [v.WEBHOOK_PLAN_READY]


def test_business_sends_nothing_even_to_a_subscribed_endpoint(client, db_session: Session, tenant: Fixture,
                                                              test_sessions, receiver: _Receiver) -> None:
    put_on_plan(db_session, tenant.organization, "business")
    db_session.commit()
    _subscribe(db_session, tenant, list(v.WEBHOOK_EVENTS))
    book = _breached_book(client, tenant)
    assert client.post(_helm_url(tenant, f"/books/{book}/plan"), headers=tenant.owner.headers).status_code == 200
    _deliver()
    assert receiver.requests == [] and _helm_events(db_session, tenant) == []


def _approved(db: Session, book: HelmBook, kind: str, params: Optional[dict] = None) -> HelmAction:
    action = HelmAction(organization_id=book.organization_id, book_id=book.id, workspace_id=book.workspace_id,
                        kind=kind, idempotency_key=f"helm:test:{uuid.uuid4().hex}", state=v.ACTION_APPROVED,
                        autonomy=v.AUTONOMY_PROPOSE, needs_approval=True, guard_passed=True,
                        title="Chase Meridian Wholesale", explanation="Keep cash above the floor.",
                        params=params or {}, undoable=kind not in v.IRREVERSIBLE_ACTIONS,
                        approvals=[{"role": "OWNER", "at": datetime.now(timezone.utc).isoformat()}])
    db.add(action)
    db.commit()
    return action


def test_an_executed_action_sends_helm_action_executed(db_session: Session, tenant: Fixture, test_sessions,
                                                        receiver: _Receiver) -> None:
    book = _book(db_session, tenant, "enterprise")
    secret = _subscribe(db_session, tenant, [v.WEBHOOK_ACTION_EXECUTED])
    action = _approved(db_session, book, v.ACTION_CREATE_TASK)
    executor.execute(db_session, action_id=action.id)
    db_session.commit()
    db_session.refresh(action)
    assert action.state == v.ACTION_EXECUTED, action.result
    _deliver()
    [envelope] = _received(secret, receiver)
    assert envelope["type"] == v.WEBHOOK_ACTION_EXECUTED
    data = envelope["data"]
    assert data["action_id"] == str(action.id) and data["kind"] == v.ACTION_CREATE_TASK and data["state"] == "EXECUTED"
    assert data["certificate_id"] == str(action.certificate_id) and len(data["certificate_sha256"]) == 64


def test_a_webhook_action_reaches_the_endpoint_listening_for_its_event(
        db_session: Session, tenant: Fixture, test_sessions, receiver: _Receiver, monkeypatch) -> None:
    from app.workers.handlers import register_all

    register_all(replace=True)
    book = _book(db_session, tenant, "enterprise")
    secret = _subscribe(db_session, tenant, [v.WEBHOOK_MANDATE_BREACHED])
    allowed, words = hands.webhook_destination(db_session, organization_id=tenant.organization.id)
    assert allowed and "hooks.acme.example.com" in words, words

    action = _approved(db_session, book, v.ACTION_WEBHOOK, {"event": v.WEBHOOK_MANDATE_BREACHED})
    executor.execute(db_session, action_id=action.id)
    db_session.commit()
    db_session.refresh(action)
    assert action.state == v.ACTION_EXECUTED, (action.result, action.guard)
    assert action.result["event"] == v.WEBHOOK_MANDATE_BREACHED and action.result["outbox_event_id"]
    _deliver()
    [envelope] = _received(secret, receiver)
    assert envelope["type"] == v.WEBHOOK_MANDATE_BREACHED and envelope["data"]["action_id"] == str(action.id)

    # Egress lockdown refusing the endpoint's host blocks the next one, in words.
    from app.core import egress

    real = egress.decide
    monkeypatch.setattr(egress, "decide", lambda channel, host, port=None, **kw: replace(
        real(channel, host, port, **kw), allowed=False, explanation="the host is not on the allow-list"))
    allowed, words = hands.webhook_destination(db_session, organization_id=tenant.organization.id)
    assert not allowed and "Egress does not allow the webhook endpoint hooks.acme.example.com" in words, words

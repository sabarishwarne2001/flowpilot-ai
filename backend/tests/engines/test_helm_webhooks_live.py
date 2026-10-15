"""Helm's outgoing webhooks (Enterprise), on real Postgres.

* Business sends none: a plan writes no Helm event, and the webhook destination says why in words.
* Enterprise writes an event only when an endpoint listens for it: an endpoint subscribed to other
  events gets nothing from Helm, and no outbox row is written for nobody.
* A `webhook` action with no endpoint listening is BLOCKED by the Guard's destination check, which
  says what to do, instead of running and sending nowhere.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.helm import HelmAction, HelmBook, HelmSettings
from app.models.outbox_event import OutboxEvent
from app.services import webhook_service
from app.services.helm import executor, hands, plan_store
from app.services.helm import vocabulary as v
from tests.conftest import Fixture
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

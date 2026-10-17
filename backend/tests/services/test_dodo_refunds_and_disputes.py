"""Refunds and disputes on Dodo payments (website and payments session, C5).

Dodo's `refund.*` and `dispute.*` events were ignored as "merchant_of_record_owned": a refunded
customer's invoice still read "Paid", RevOps never counted the money given back, and a chargeback
went unnoticed. Now, re-fetching the payment each time (D-9):

* a refund is recorded on the payment's invoice ("Refunded" or "Partially refunded · $X", as
  N-069 does for Stripe), owners and billing members are told (billing notices are always
  emailed, N-062), and RevOps counts it;
* a dispute is recorded, the operator is alerted and the organization told;
* a lost dispute (the bank reversed the payment) makes the organization read-only, with the reason
  "payment reversed by your bank", until a later payment succeeds or the operator lifts it.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.core.security import create_access_token
from app.models.billing_dispute import BillingDispute
from app.models.notification import Notification
from app.models.operator_alert import OperatorAlert
from app.services.billing import dunning_service
from app.services.revops import metrics as revops_metrics
from tests.services.test_dodo_invoices import invoices
from tests.services.test_dodo_reconciliation import (  # noqa: F401 - fixtures
    deliver,
    dodo,
    org,
    subscribe,
)

RESTRICTED = dunning_service.BillingAccessState.RESTRICTED
ACTIVE = dunning_service.BillingAccessState.ACTIVE


def _payment(dodo, sub_id: str) -> str:
    return next(p["payment_id"] for p in dodo.payments.values() if p["subscription_id"] == sub_id)


def _titles(db, organization_id) -> list[str]:
    db.expire_all()
    return list(db.execute(select(Notification.title).where(Notification.organization_id == organization_id)
                           .order_by(Notification.created_at)).scalars())


# ---------------------------------------------------------------------------- refunds


def test_a_full_refund_is_recorded_told_and_counted(dodo, client, db, org) -> None:
    sub_id = subscribe(dodo, client, db, org, seats=2)
    payment = _payment(dodo, sub_id)
    dodo.refund(payment)
    assert ("refund.succeeded", "processed") in deliver(dodo, client, db)
    [invoice] = invoices(db, org["organization"].id)
    assert invoice.amount_refunded_micros == invoice.amount_paid_micros and invoice.refunded_at is not None
    assert any(t.startswith("Refund of") for t in _titles(db, org["organization"].id))
    figures = revops_metrics.compute(db, at=datetime.now(timezone.utc) + timedelta(minutes=1))
    assert figures["refunds"]["USD"]["refunded_micros"] >= invoice.amount_refunded_micros


def test_partial_refunds_add_up_and_a_replay_or_a_failed_refund_changes_nothing(dodo, client, db, org) -> None:
    sub_id = subscribe(dodo, client, db, org, seats=2)
    payment = _payment(dodo, sub_id)
    dodo.refund(payment, amount=1_000)
    copies = list(dodo.outbox)
    deliver(dodo, client, db)
    for delivery in copies:
        client.post("/api/v1/billing/webhooks/dodo", content=delivery.body, headers=delivery.headers)
    deliver(dodo, client, db)
    [invoice] = invoices(db, org["organization"].id)
    assert invoice.amount_refunded_micros == 1_000 * 10_000
    dodo.refund(payment, amount=500, succeed=False)
    deliver(dodo, client, db)
    assert invoices(db, org["organization"].id)[0].amount_refunded_micros == 1_000 * 10_000
    dodo.refund(payment, amount=2_000)
    deliver(dodo, client, db)
    assert invoices(db, org["organization"].id)[0].amount_refunded_micros == 3_000 * 10_000
    notices = [t for t in _titles(db, org["organization"].id) if t.startswith("Refund of")]
    assert len(notices) == 2


def test_a_refund_arriving_before_its_payment_is_still_recorded_once(dodo, client, db, org) -> None:
    checkout = dodo.checkout(product_id="pdt_business", quantity=1, email=org["owner"].email,
                             metadata={"organization_id": str(org["organization"].id)})
    sub = dodo.pay(checkout)
    subscription_events = list(dodo.outbox)
    dodo.outbox.clear()
    dodo.refund(_payment(dodo, sub["subscription_id"]))
    refund_events = list(dodo.outbox)
    dodo.outbox[:] = [subscription_events[0], *refund_events, *subscription_events[1:]]
    outcomes = deliver(dodo, client, db)
    assert not [o for o in outcomes if o[1].startswith("failed")], outcomes
    [invoice] = invoices(db, org["organization"].id)
    assert invoice.amount_refunded_micros == invoice.amount_paid_micros


# ---------------------------------------------------------------------------- disputes


def _summary(client, org) -> dict:
    headers = {"Authorization": f"Bearer {create_access_token(subject=org['owner'].id)}"}
    answer = client.get(f"/api/v1/organizations/{org['organization'].id}/billing/access-summary", headers=headers)
    assert answer.status_code == 200, answer.text
    return answer.json()


def test_a_dispute_is_recorded_and_the_operator_and_organization_are_told(dodo, client, db, org) -> None:
    sub_id = subscribe(dodo, client, db, org)
    dodo.dispute(_payment(dodo, sub_id), status="dispute_opened")
    assert ("dispute.opened", "processed") in deliver(dodo, client, db)
    [dispute] = db.execute(select(BillingDispute)).scalars().all()
    assert (dispute.gateway, dispute.status, dispute.reversed_at) == ("DODO", "dispute_opened", None)
    assert dispute.invoice_id == invoices(db, org["organization"].id)[0].id
    alerts = db.execute(select(OperatorAlert).where(OperatorAlert.kind == "PAYMENT_DISPUTED")).scalars().all()
    assert len(alerts) == 1
    assert any("disputed" in t.lower() for t in _titles(db, org["organization"].id))
    assert dunning_service.access_state(db, organization_id=org["organization"].id) is ACTIVE


def test_a_lost_dispute_makes_the_organization_read_only_until_a_later_payment(dodo, client, db, org) -> None:
    organization_id = org["organization"].id
    sub_id = subscribe(dodo, client, db, org)
    payment = _payment(dodo, sub_id)
    dispute = dodo.dispute(payment, status="dispute_opened")
    deliver(dodo, client, db)
    dodo.dispute(payment, status="dispute_lost", dispute_id=dispute["dispute_id"])
    assert ("dispute.lost", "processed") in deliver(dodo, client, db)

    assert dunning_service.access_state(db, organization_id=organization_id) is RESTRICTED
    summary = _summary(client, org)
    assert summary["state"] == "RESTRICTED" and summary["is_read_only"] is True
    assert summary["reason"] == "PAYMENT_REVERSED"
    assert any("reversed by your bank" in t.lower() for t in _titles(db, organization_id))
    assert db.execute(select(OperatorAlert).where(OperatorAlert.kind == "PAYMENT_REVERSED")).scalars().all()

    # The next renewal is paid: the hold lifts by itself.
    dodo.clock = lambda: datetime.now(timezone.utc) + timedelta(days=31)
    dodo.renew(sub_id)
    deliver(dodo, client, db)
    assert dunning_service.access_state(db, organization_id=organization_id) is ACTIVE
    db.expire_all()
    [row] = db.execute(select(BillingDispute)).scalars().all()
    assert row.cleared_at is not None and "payment" in row.cleared_reason
    assert _summary(client, org).get("reason") is None


def test_a_won_dispute_never_restricts(dodo, client, db, org) -> None:
    sub_id = subscribe(dodo, client, db, org)
    payment = _payment(dodo, sub_id)
    dispute = dodo.dispute(payment)
    dodo.dispute(payment, status="dispute_won", dispute_id=dispute["dispute_id"])
    deliver(dodo, client, db)
    assert dunning_service.access_state(db, organization_id=org["organization"].id) is ACTIVE
    [row] = db.execute(select(BillingDispute)).scalars().all()
    assert row.status == "dispute_won" and row.reversed_at is None


def test_the_operator_lifts_a_reversal_from_the_platform_admin(dodo, client, db, org) -> None:
    from app.models.user import User

    sub_id = subscribe(dodo, client, db, org)
    payment = _payment(dodo, sub_id)
    dodo.dispute(payment, status="dispute_lost")
    deliver(dodo, client, db)
    [row] = db.execute(select(BillingDispute)).scalars().all()

    admin = User(email="operator@servabo.test", hashed_password="!x", is_active=True, is_superuser=True,
                 email_verified_at=datetime.now(timezone.utc))
    db.add(admin)
    db.flush()
    headers = {"Authorization": f"Bearer {create_access_token(subject=admin.id)}"}
    listed = client.get("/api/v1/admin/revops/disputes", headers=headers)
    assert listed.status_code == 200 and listed.json()[0]["id"] == str(row.id)
    assert listed.json()[0]["holding_read_only"] is True

    owner = {"Authorization": f"Bearer {create_access_token(subject=org['owner'].id)}"}
    assert client.post(f"/api/v1/admin/revops/disputes/{row.id}/lift", json={"reason": "x"},
                       headers=owner).status_code == 404
    lifted = client.post(f"/api/v1/admin/revops/disputes/{row.id}/lift",
                         json={"reason": "Customer paid by bank transfer"}, headers=headers)
    assert lifted.status_code == 200, lifted.text
    assert lifted.json()["holding_read_only"] is False
    assert dunning_service.access_state(db, organization_id=org["organization"].id) is ACTIVE

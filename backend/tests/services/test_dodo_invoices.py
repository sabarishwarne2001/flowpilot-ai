"""A Dodo customer's payments are their invoices (website and payments session, C4).

Only Stripe's `invoice.*` events created invoice records, so a Dodo customer's Billing → Invoices
was empty. Each Dodo payment (`payment.succeeded`) is now recorded as a paid invoice row: the amount,
tax, currency and period Dodo charged, and Dodo's payment id. Its tax invoice is Dodo's (merchant of
record): an owner/billing-only route fetches Dodo's PDF server-side and streams it.

And a defect found on the way: the period-close backstop assembled an OPEN statement for a Dodo
subscription whose renewal webhook was late, and the dunning sweep chases OPEN invoices, so a paying
Dodo customer could be made read-only. Dodo delinquency is the subscription's own state (D-11); the
backstop no longer opens statements for Dodo subscriptions.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.core.security import create_access_token
from app.models.invoice import Invoice, InvoiceStatus
from app.models.organization import MembershipStatus, OrganizationMember, OrganizationRole
from app.models.user import User
from app.services.billing import period_close_service
from tests.services.test_dodo_reconciliation import (  # noqa: F401 - fixtures
    BUSINESS_PRICE,
    deliver,
    dodo,
    org,
    row,
    subscribe,
)


def invoices(db, organization_id) -> list[Invoice]:
    from app.models.billing_account import BillingAccount

    db.expire_all()
    return list(db.execute(
        select(Invoice).join(BillingAccount, BillingAccount.id == Invoice.billing_account_id)
        .where(BillingAccount.organization_id == organization_id).order_by(Invoice.created_at)).scalars())


def payment_ids(dodo, subscription_id: str) -> list[str]:
    return [p["payment_id"] for p in dodo.payments.values()
            if p["subscription_id"] == subscription_id and p["status"] == "succeeded"]


def test_a_payment_at_checkout_is_a_paid_invoice_with_dodos_amounts(dodo, client, db, org) -> None:
    dodo.tax_rate = 0.18
    sub_id = subscribe(dodo, client, db, org, seats=3)
    [invoice] = invoices(db, org["organization"].id)
    [payment] = payment_ids(dodo, sub_id)
    pre_tax = BUSINESS_PRICE * 3
    tax = round(pre_tax * 0.18)
    assert (invoice.gateway, invoice.gateway_invoice_id, invoice.status) == ("DODO", payment, InvoiceStatus.PAID)
    assert (invoice.subtotal_micros, invoice.tax_micros, invoice.total_micros) == (
        pre_tax * 10_000, tax * 10_000, (pre_tax + tax) * 10_000)
    assert invoice.amount_paid_micros == invoice.total_micros and invoice.paid_at is not None
    assert invoice.currency == "USD" and invoice.seats_billed == 3
    subscription = row(db, sub_id)
    assert (invoice.period_start, invoice.period_end) == (subscription.current_period_start,
                                                          subscription.current_period_end)
    assert invoice.subscription_id == subscription.id
    assert invoice.finalized_at is not None and invoice.content_digest.startswith("sha256:")
    assert invoice.assembly_notes["issued_by"] == "Dodo Payments (merchant of record)"


def test_every_payment_is_one_invoice_and_a_replay_adds_none(dodo, client, db, org) -> None:
    sub_id = subscribe(dodo, client, db, org, seats=1)
    later = datetime.now(timezone.utc) + timedelta(days=31)
    dodo.clock = lambda: later
    dodo.renew(sub_id)
    renewal = list(dodo.outbox)
    deliver(dodo, client, db)
    for delivery in renewal:  # Dodo delivers the renewal again
        client.post("/api/v1/billing/webhooks/dodo", content=delivery.body, headers=delivery.headers)
    deliver(dodo, client, db)
    rows = invoices(db, org["organization"].id)
    assert [r.gateway_invoice_id for r in rows] == payment_ids(dodo, sub_id)
    assert rows[1].period_start > rows[0].period_start


def test_a_plan_change_charge_is_its_own_invoice_inside_the_period(dodo, client, db, org) -> None:
    sub_id = subscribe(dodo, client, db, org, seats=1)
    dodo.clock = lambda: datetime.now(timezone.utc) + timedelta(days=10)
    dodo.handle("POST", f"/subscriptions/{sub_id}/change-plan",
                {"product_id": "pdt_business", "quantity": 4, "proration_billing_mode": "prorated_immediately"})
    deliver(dodo, client, db)
    first, change = invoices(db, org["organization"].id)
    assert change.status is InvoiceStatus.PAID and change.total_micros > 0
    assert first.period_start < change.period_start < change.period_end
    assert "Plan change" in change.line_items[0].description


def test_a_failed_payment_makes_no_invoice(dodo, client, db, org) -> None:
    sub_id = subscribe(dodo, client, db, org, seats=1)
    dodo.renew(sub_id, succeed=False)
    deliver(dodo, client, db)
    assert len(invoices(db, org["organization"].id)) == 1


# ---------------------------------------------------------------------------- the download route


def _member(db, org, role: OrganizationRole) -> dict[str, str]:
    import uuid as _uuid

    person = User(email=f"{role.value.lower()}-{_uuid.uuid4().hex[:6]}@acme.test", hashed_password="!x",
                  is_active=True, email_verified_at=datetime.now(timezone.utc))
    db.add(person)
    db.flush()
    db.add(OrganizationMember(organization_id=org["organization"].id, user_id=person.id, role=role,
                              status=MembershipStatus.ACTIVE))
    db.flush()
    return {"Authorization": f"Bearer {create_access_token(subject=person.id)}"}


def test_the_owner_downloads_dodos_tax_invoice(dodo, client, db, org) -> None:
    subscribe(dodo, client, db, org, seats=1)
    [invoice] = invoices(db, org["organization"].id)
    headers = {"Authorization": f"Bearer {create_access_token(subject=org['owner'].id)}"}
    base = f"/api/v1/organizations/{org['organization'].id}/invoices"

    listed = client.get(base, headers=headers).json()["invoices"][0]
    assert listed["gateway"] == "DODO" and listed["downloadable"] is True
    assert listed["issued_by"] == "Dodo Payments (merchant of record)"

    pdf = client.get(f"{base}/{invoice.id}/download", headers=headers)
    assert pdf.status_code == 200, pdf.text
    assert pdf.headers["content-type"] == "application/pdf" and pdf.content.startswith(b"%PDF")
    assert invoice.number in pdf.headers["content-disposition"]
    assert ("GET", f"/invoices/payments/{invoice.gateway_invoice_id}", None) in dodo.calls

    billing = _member(db, org, OrganizationRole.BILLING)
    assert client.get(f"{base}/{invoice.id}/download", headers=billing).status_code == 200
    member = _member(db, org, OrganizationRole.MEMBER)
    assert client.get(f"{base}/{invoice.id}/download", headers=member).status_code == 403


def test_dodo_unavailable_is_a_clear_503_not_a_server_error(dodo, client, db, org) -> None:
    subscribe(dodo, client, db, org, seats=1)
    [invoice] = invoices(db, org["organization"].id)
    dodo.failures["/invoices/payments/"] = 502
    headers = {"Authorization": f"Bearer {create_access_token(subject=org['owner'].id)}"}
    answer = client.get(f"/api/v1/organizations/{org['organization'].id}/invoices/{invoice.id}/download",
                        headers=headers)
    assert answer.status_code == 503 and "Dodo Payments" in answer.json()["detail"]


# ---------------------------------------------------------------------------- the period backstop


def test_the_backstop_never_opens_an_unpaid_statement_for_a_dodo_subscription(dodo, client, db, org) -> None:
    sub_id = subscribe(dodo, client, db, org, seats=1)
    subscription = row(db, sub_id)
    # The renewal webhook is late: the period Servabo knows has ended.
    subscription.current_period_end = datetime.now(timezone.utc) - timedelta(days=2)
    subscription.current_period_start = subscription.current_period_end - timedelta(days=30)
    db.flush()
    targets = period_close_service.backstop_targets(db, cutoff=datetime.now(timezone.utc), limit=50)
    assert subscription.id not in targets

    # Control: the same overdue period on a Stripe subscription is still closed by the backstop.
    subscription.gateway, subscription.stripe_subscription_id = "STRIPE", "sub_control"
    db.flush()
    assert subscription.id in period_close_service.backstop_targets(db, cutoff=datetime.now(timezone.utc), limit=50)

"""Customer details reach the Dodo customer, and never Stripe (website and payments session, C9).

The legal name is what the payment provider prints on invoices and receipts (F-365), and the
billing email is where they go. On a Dodo account a change of either was not sent anywhere (the
name) or sent to Stripe (the email: `update_billing_email` always called the Stripe gateway, with no
Stripe customer to update). Both now `PATCH /customers/{id}` at Dodo (name, email), and Stripe is
never called on a Dodo account.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.models.billing_account import BillingAccount
from app.services.billing import account_service, stripe_gateway
from tests.services.test_dodo_reconciliation import dodo, org, subscribe  # noqa: F401 - fixtures


@pytest.fixture()
def no_stripe(monkeypatch):
    def refuse(*_a, **_k):
        raise AssertionError("Stripe was called for a Dodo customer")

    monkeypatch.setattr(stripe_gateway, "get_gateway", refuse)


def _customer(dodo, db, org) -> dict:
    account = db.execute(select(BillingAccount).where(
        BillingAccount.organization_id == org["organization"].id)).scalar_one()
    return dodo.customers[account.gateway_customer_id]


def test_a_new_legal_name_reaches_the_dodo_customer(dodo, client, db, org, no_stripe) -> None:
    subscribe(dodo, client, db, org)
    organization = org["organization"]
    organization.legal_name = "Acme Industries Private Limited"
    db.flush()
    assert account_service.push_customer_name(db, organization=organization) is True
    assert _customer(dodo, db, org)["name"] == "Acme Industries Private Limited"
    patches = [b for m, p, b in dodo.calls if m == "PATCH" and p.startswith("/customers/")]
    assert patches == [{"name": "Acme Industries Private Limited"}]


def test_a_new_billing_email_reaches_the_dodo_customer(dodo, client, db, org, no_stripe) -> None:
    subscribe(dodo, client, db, org)
    account_service.update_billing_email(db, organization_id=org["organization"].id,
                                         billing_email="Accounts@Acme.test")
    assert _customer(dodo, db, org)["email"] == "accounts@acme.test"


def test_dodo_unreachable_keeps_the_local_change_and_says_so(dodo, client, db, org, no_stripe) -> None:
    subscribe(dodo, client, db, org)
    dodo.failures["/customers/"] = 503
    account = account_service.update_billing_email(db, organization_id=org["organization"].id,
                                                   billing_email="ap@acme.test")
    assert account.billing_email == "ap@acme.test"

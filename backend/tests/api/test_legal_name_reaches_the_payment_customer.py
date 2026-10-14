"""A later change of the legal name reaches the payment provider's customer (campaign session 4, F-365).

The organization's legal name is what Stripe prints on its invoices and receipts: the customer is
created with `legal_name or name`. It was sent only when the customer was first created, so an
organization that corrected or registered its legal name afterwards kept being invoiced under the
old one, while every FlowPilot screen showed the new one. Decided (campaign session 4): a later
change must update the provider's customer too.

Stripe is a recording stand-in installed with `stripe_gateway.set_gateway` (as in the other
billing tests); no request leaves the process. The gateway's own call is checked against a
stand-in Stripe client, never the network.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Optional

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.services.billing import account_service, stripe_gateway
from app.services.billing.stripe_gateway import StripeCustomerSnapshot
from tests.conftest import Fixture

API = "/api/v1"


class _Stripe:
    def __init__(self) -> None:
        self.created: list[dict[str, Any]] = []
        self.renamed: list[tuple[str, str]] = []
        self.fail_rename = False

    def create_customer(self, *, organization_id, email, name=None, currency=None, metadata=None):
        self.created.append({"organization_id": organization_id, "name": name})
        return StripeCustomerSnapshot(id="cus_test_f365", email=email, currency=currency)

    def update_customer_name(self, *, customer_id: str, name: str) -> StripeCustomerSnapshot:
        if self.fail_rename:
            raise stripe_gateway.StripeTransientError("stripe unreachable")
        self.renamed.append((customer_id, name))
        return StripeCustomerSnapshot(id=customer_id, email=None, currency=None)


@pytest.fixture()
def stripe() -> _Stripe:
    fake = _Stripe()
    previous = stripe_gateway.set_gateway(fake)
    yield fake
    stripe_gateway.set_gateway(previous)


def _customer(db_session: Session, tenant: Fixture) -> None:
    account_service.ensure_billing_account(db_session, organization_id=tenant.organization.id)
    db_session.commit()


def _patch(client: TestClient, tenant: Fixture, **body: Optional[str]) -> dict:
    response = client.patch(f"{API}/organizations/{tenant.organization.id}", json=body,
                            headers=tenant.owner.headers)
    assert response.status_code == 200, response.text
    return response.json()


def test_a_later_legal_name_is_pushed_to_the_customer(client, db_session, tenant: Fixture, stripe: _Stripe) -> None:
    _patch(client, tenant, legal_name="Acme Supplies Limited")
    _customer(db_session, tenant)
    assert [c["name"] for c in stripe.created] == ["Acme Supplies Limited"]

    _patch(client, tenant, legal_name="Acme Supplies Private Limited")
    assert stripe.renamed == [("cus_test_f365", "Acme Supplies Private Limited")], (
        "the payment provider still invoices under the old legal name")

    # Saving it again unchanged, or renaming the organization while a legal name is set, changes
    # nothing the provider prints: nothing is sent.
    _patch(client, tenant, legal_name="Acme Supplies Private Limited")
    _patch(client, tenant, name="Acme (EMEA)")
    assert len(stripe.renamed) == 1, stripe.renamed


def test_without_a_legal_name_the_display_name_is_kept_in_step(client, db_session, tenant: Fixture,
                                                                 stripe: _Stripe) -> None:
    _customer(db_session, tenant)
    assert stripe.created[0]["name"] == tenant.organization.name
    _patch(client, tenant, name="Contoso Trading")
    assert stripe.renamed == [("cus_test_f365", "Contoso Trading")]


def test_no_customer_yet_means_nothing_to_update(client, tenant: Fixture, stripe: _Stripe) -> None:
    _patch(client, tenant, legal_name="Acme Supplies Limited")
    assert stripe.renamed == [] and stripe.created == []


def test_an_unreachable_provider_does_not_lose_the_change(client, db_session, tenant: Fixture,
                                                          stripe: _Stripe) -> None:
    _customer(db_session, tenant)
    stripe.fail_rename = True
    saved = _patch(client, tenant, legal_name="Acme Supplies Limited")
    assert saved["legal_name"] == "Acme Supplies Limited"


def test_the_gateway_sends_only_the_name(monkeypatch) -> None:
    calls: list[tuple] = []
    client = SimpleNamespace(v1=SimpleNamespace(customers=SimpleNamespace(
        update=lambda customer_id, params, options: calls.append((customer_id, params)) or {
            "id": customer_id, "name": params["name"]})))
    gateway = stripe_gateway.StripeGateway(api_key="sk_test_not_a_real_key")
    gateway._client = client  # noqa: SLF001 - a stand-in Stripe client: nothing leaves the process
    snapshot = gateway.update_customer_name(customer_id="cus_test_f365", name="Acme Supplies Limited")
    assert calls == [("cus_test_f365", {"name": "Acme Supplies Limited"})]
    assert snapshot.id == "cus_test_f365"

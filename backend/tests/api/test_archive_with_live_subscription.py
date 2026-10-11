"""Deleting an organization never leaves its subscription charging (campaign session 1, F).

    pytest tests/api/test_archive_with_live_subscription.py -q

Archiving is how an owner deletes an organization. It turned off members' access and
the API keys but left a paid subscription running, so the customer went on being
charged every month for an organization they had deleted. While a paid subscription is
live and renews, archiving is refused with a 409 that says to cancel it in the billing
portal first; once it is cancelled (at period end or ended), archiving works as before.
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.orm import Session

from tests.api.test_seat_purchase_api import _headers, _on_business, fake_gateway  # noqa: F401 - fixture


def _archive(client, tenant):
    return client.post(
        f"/api/v1/organizations/{tenant.organization.id}/archive",
        json={"confirm_slug": tenant.organization.slug},
        headers=_headers(tenant.owner.user),
    )


def test_an_organization_that_still_pays_is_not_archived(
    client, db_session: Session, tenant, fake_gateway  # noqa: F811
) -> None:
    _on_business(db_session, tenant, fake_gateway, seats=5)
    refused = _archive(client, tenant)
    assert refused.status_code == 409, refused.text
    body = refused.json()
    assert body["code"] == "SUBSCRIPTION_STILL_ACTIVE"
    assert "billing portal" in body["message"]
    db_session.refresh(tenant.organization)
    assert tenant.organization.status.value == "ACTIVE"


def test_once_the_subscription_will_not_renew_the_organization_can_be_archived(
    client, db_session: Session, tenant, fake_gateway  # noqa: F811
) -> None:
    _on_business(db_session, tenant, fake_gateway, seats=5)
    db_session.execute(text("UPDATE subscriptions SET cancel_at_period_end = true, cancel_at = now() + interval '20 days'"))
    db_session.commit()
    assert _archive(client, tenant).status_code == 200


def test_once_the_subscription_has_ended_the_organization_can_be_archived(
    client, db_session: Session, tenant, fake_gateway  # noqa: F811
) -> None:
    _on_business(db_session, tenant, fake_gateway, seats=5)
    db_session.execute(text("UPDATE subscriptions SET status = 'canceled'::subscription_status, canceled_at = now()"))
    db_session.commit()
    archived = _archive(client, tenant)
    assert archived.status_code == 200, archived.text

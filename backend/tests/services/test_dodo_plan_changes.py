"""Plan changes inside the app, on Dodo (website and payments session, C6).

With a subscription, "Change plan" opened the billing portal, which is Stripe's way; Dodo's customer
portal manages the payment method, invoices and cancellation, not plans. Plan changes now go
through Dodo's change-plan API, and its preview first:

* the owner sees the exact amount charged now and when the change takes effect, before confirming;
* an upgrade takes effect at once, charged `prorated_immediately` (only the days left, at the
  difference: never twice for the same days) and refused if the charge fails
  (`on_payment_failure: prevent_change`); the confirmed amount must be the previewed one;
* a downgrade takes effect at the next renewal (`do_not_bill`, applied just before it, so the
  renewal is billed at the new price) and is refused first while seats in use or workspaces
  exceed the new plan (N-003);
* owners only.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.core.plan_limits import SEATS_LIMIT, WORKSPACES_LIMIT
from app.core.security import create_access_token
from app.models.organization import MembershipStatus, OrganizationMember, OrganizationRole
from app.models.quota_tier import QuotaTier, QuotaTierEntry
from app.models.user import User
from app.models.workspace import Workspace, WorkspaceStatus
from app.services.billing import plan_change_service
from tests.services.test_dodo_reconciliation import (  # noqa: F401 - fixtures
    BUSINESS_PRICE,
    DEVELOPER_PRICE,
    deliver,
    dodo,
    org,
    row,
    subscribe,
)


def _developer_with_limits(db, limits: dict[str, int]) -> None:
    """A new version of the Developer plan with these limits (a published version is immutable)."""
    current = db.execute(select(QuotaTier).where(QuotaTier.key == "developer", QuotaTier.is_active.is_(True))
                         .order_by(QuotaTier.version.desc()).limit(1)).scalar_one()
    at = datetime(2026, 2, 1, tzinfo=timezone.utc)
    tier = QuotaTier(key="developer", display_name="Developer", version=current.version + 1, effective_from=at,
                     published_at=None, is_active=False, gateway_price_id=current.gateway_price_id,
                     unit_amount_micros=current.unit_amount_micros, currency="USD", billing_interval="month")
    db.add(tier)
    db.flush()
    for limit_key, value in limits.items():
        db.add(QuotaTierEntry(quota_tier_id=tier.id, limit_key=limit_key, max_quantity=Decimal(value),
                              overage_policy="REFUSE"))
    db.flush()
    tier.published_at = at
    tier.is_active = True
    db.flush([tier])
    from app.services import quota_service

    quota_service.clear_cache()  # plans are cached per process


def _owner(org) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(subject=org['owner'].id)}"}


def _url(org, tail: str = "") -> str:
    return f"/api/v1/organizations/{org['organization'].id}/billing/plan-change{tail}"


def _changes(dodo, sub_id: str) -> list[dict]:
    return [body for method, path, body in dodo.calls
            if method == "POST" and path == f"/subscriptions/{sub_id}/change-plan"]


# ---------------------------------------------------------------------------- upgrades


def test_an_upgrade_shows_the_amount_and_takes_effect_at_once(dodo, client, db, org) -> None:
    sub_id = subscribe(dodo, client, db, org, product="pdt_developer", seats=2)
    dodo.clock = lambda: datetime.now(timezone.utc) + timedelta(days=15)
    preview = client.post(_url(org, "/preview"), json={"quota_tier_key": "business", "seats": 2}, headers=_owner(org))
    assert preview.status_code == 200, preview.text
    shown = preview.json()
    assert shown["direction"] == "UPGRADE" and shown["effective"] == "NOW"
    # Half a period left: about half the difference, never the full price again.
    difference = (BUSINESS_PRICE - DEVELOPER_PRICE) * 2
    assert 0 < shown["amount_now_micros"] < difference * 10_000
    assert shown["currency"] == "USD" and shown["blockers"] == []

    applied = client.post(_url(org), json={"quota_tier_key": "business", "seats": 2,
                                           "expected_amount_micros": shown["amount_now_micros"]}, headers=_owner(org))
    assert applied.status_code == 200, applied.text
    [change] = _changes(dodo, sub_id)
    assert change["proration_billing_mode"] == "prorated_immediately"
    assert change["on_payment_failure"] == "prevent_change"
    assert row(db, sub_id).quota_tier_key == "business"
    deliver(dodo, client, db)  # Dodo's own events agree
    assert row(db, sub_id).quota_tier_key == "business"


def test_a_confirmation_at_a_different_amount_is_refused(dodo, client, db, org) -> None:
    sub_id = subscribe(dodo, client, db, org, product="pdt_developer", seats=1)
    answer = client.post(_url(org), json={"quota_tier_key": "business", "seats": 1, "expected_amount_micros": 1},
                         headers=_owner(org))
    assert answer.status_code == 409 and "amount" in answer.json()["detail"].lower()
    assert _changes(dodo, sub_id) == []


# ---------------------------------------------------------------------------- downgrades


def test_a_downgrade_waits_for_the_renewal_and_is_billed_at_the_new_price(dodo, client, db, org) -> None:
    sub_id = subscribe(dodo, client, db, org, product="pdt_business", seats=1)
    preview = client.post(_url(org, "/preview"), json={"quota_tier_key": "developer", "seats": 1},
                          headers=_owner(org)).json()
    period_end = row(db, sub_id).current_period_end
    assert preview["direction"] == "DOWNGRADE" and preview["effective"] == "AT_RENEWAL"
    assert preview["amount_now_micros"] == 0
    assert datetime.fromisoformat(preview["effective_at"].replace("Z", "+00:00")) == period_end
    assert preview["next_renewal_micros"] == DEVELOPER_PRICE * 10_000

    applied = client.post(_url(org), json={"quota_tier_key": "developer", "seats": 1, "expected_amount_micros": 0},
                          headers=_owner(org))
    assert applied.status_code == 200, applied.text
    assert _changes(dodo, sub_id) == [] and row(db, sub_id).quota_tier_key == "business"
    assert row(db, sub_id).pending_quota_tier_key == "developer"

    # Not yet: the sweep leaves it until the period is about to end.
    assert plan_change_service.apply_due(db, now=datetime.now(timezone.utc)) == []
    applied_now = plan_change_service.apply_due(db, now=period_end - timedelta(minutes=30))
    assert [str(a["subscription_id"]) for a in applied_now] == [str(row(db, sub_id).id)]
    [change] = _changes(dodo, sub_id)
    assert (change["product_id"], change["proration_billing_mode"]) == ("pdt_developer", "do_not_bill")
    subscription = row(db, sub_id)
    assert subscription.quota_tier_key == "developer" and subscription.pending_quota_tier_key is None
    assert dodo.subscriptions[sub_id]["recurring_pre_tax_amount"] == DEVELOPER_PRICE


def test_a_downgrade_is_refused_while_the_new_plan_would_not_fit(dodo, client, db, org) -> None:
    subscribe(dodo, client, db, org, product="pdt_business", seats=3)
    _developer_with_limits(db, {WORKSPACES_LIMIT: 1, SEATS_LIMIT: 1})
    for slug in ("one", "two"):
        db.add(Workspace(organization_id=org["organization"].id, slug=slug, workspace_name=slug.title(),
                         status=WorkspaceStatus.ACTIVE))
    for n in range(2):
        person = User(email=f"member{n}@acme.test", hashed_password="!x", is_active=True)
        db.add(person)
        db.flush()
        db.add(OrganizationMember(organization_id=org["organization"].id, user_id=person.id,
                                  role=OrganizationRole.MEMBER, status=MembershipStatus.ACTIVE))
    db.flush()
    preview = client.post(_url(org, "/preview"), json={"quota_tier_key": "developer", "seats": 1},
                          headers=_owner(org)).json()
    codes = {b["code"] for b in preview["blockers"]}
    assert codes == {"SEATS_IN_USE", "WORKSPACES_OVER_LIMIT"}, preview["blockers"]
    assert all(b["message"] for b in preview["blockers"])
    refused = client.post(_url(org), json={"quota_tier_key": "developer", "seats": 1, "expected_amount_micros": 0},
                          headers=_owner(org))
    assert refused.status_code == 409


def test_a_scheduled_downgrade_can_be_withdrawn(dodo, client, db, org) -> None:
    sub_id = subscribe(dodo, client, db, org, product="pdt_business", seats=1)
    client.post(_url(org), json={"quota_tier_key": "developer", "seats": 1, "expected_amount_micros": 0},
                headers=_owner(org))
    assert client.delete(_url(org), headers=_owner(org)).status_code == 200
    assert row(db, sub_id).pending_quota_tier_key is None
    assert plan_change_service.apply_due(db, now=row(db, sub_id).current_period_end) == []


# ---------------------------------------------------------------------------- who may


@pytest.mark.parametrize("role", [OrganizationRole.ADMIN, OrganizationRole.BILLING, OrganizationRole.MEMBER])
def test_only_an_owner_changes_the_plan(dodo, client, db, org, role) -> None:
    subscribe(dodo, client, db, org, product="pdt_developer", seats=1)
    person = User(email=f"{role.value.lower()}@acme.test", hashed_password="!x", is_active=True)
    db.add(person)
    db.flush()
    db.add(OrganizationMember(organization_id=org["organization"].id, user_id=person.id, role=role,
                              status=MembershipStatus.ACTIVE))
    db.flush()
    headers = {"Authorization": f"Bearer {create_access_token(subject=person.id)}"}
    assert client.post(_url(org, "/preview"), json={"quota_tier_key": "business", "seats": 1},
                       headers=headers).status_code == 403
    assert client.post(_url(org), json={"quota_tier_key": "business", "seats": 1, "expected_amount_micros": 0},
                       headers=headers).status_code == 403

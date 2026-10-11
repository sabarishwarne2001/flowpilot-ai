"""Add-ons can be bought, are honoured, are billed once and can be removed safely (campaign session 1, D.6).

    pytest tests/services/test_addon_lifecycle.py -q

The add-on ledger (`addon_service`) had no tests. These drive it the way the gateway
does (`record_purchase_state` with the re-fetched subscription, then
`reconcile_organization`), on a Free organization (no plan bundles either add-on) and on
Developer (which bundles custom domains):

  * a purchase grants the add-on; a cancellation with a live custom domain starts a
    14-day grace (the domain keeps working); after it the domain is taken offline but
    the claim is kept; buying again restores the add-on and does NOT silently put the
    domain back online (it must be re-verified);
  * a cancellation with nothing built on the add-on lapses at once;
  * an older gateway state arriving after a newer one changes nothing;
  * a second live subscription for the same add-on is refused as a duplicate charge;
  * a plan that bundles the add-on keeps it when a purchase is cancelled, and a
    downgrade off that plan follows the same grace as a cancellation;
  * a checkout for an add-on the organization already has is refused, so nobody pays
    twice; on a gateway that cannot sell add-ons the refusal says what to do.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.orm import Session

from app.core import entitlements
from app.models.custom_domain import DOMAIN_STATUS_REVOKED, DOMAIN_STATUS_VERIFIED, CustomDomain
from app.models.organization_addon import ADDON_STATUS_ACTIVE, ADDON_STATUS_GRACE, ADDON_STATUS_LAPSED
from app.services.billing import addon_service, entitlement_service
from tests.security.plans import put_on_plan

DOMAIN = entitlements.CUSTOM_DOMAIN_ADDON
NOW = datetime(2026, 10, 11, 12, 0, tzinfo=timezone.utc)


@pytest.fixture()
def free(db_session: Session, tenant):
    put_on_plan(db_session, tenant.organization, "free")
    return tenant.organization


def _purchase(db: Session, org_id: uuid.UUID, status: str, version: int, *, sub: str = "sub_addon_1", at=NOW):
    addon_service.record_purchase_state(
        db, organization_id=org_id, addon_key=DOMAIN, gateway="DODO",
        gateway_subscription_id=sub, gateway_status=status, state_version=version, now=at,
    )
    return addon_service.reconcile_organization(db, organization_id=org_id, now=at, only=DOMAIN)[0]


def _ledger(db: Session, org_id: uuid.UUID):
    return entitlement_service.ledger_row(db, organization_id=org_id, addon_key=DOMAIN)


def _verified_domain(db: Session, org_id: uuid.UUID, hostname: str = "docs.acme-addon.example") -> CustomDomain:
    domain = CustomDomain(
        organization_id=org_id, hostname=hostname, status=DOMAIN_STATUS_VERIFIED,
        challenge_token="t" * 32, challenge_issued_at=NOW, challenge_expires_at=NOW + timedelta(days=1),
        verified_at=NOW,
    )
    db.add(domain)
    db.flush()
    return domain


def test_a_purchase_grants_and_a_cancellation_keeps_a_live_domain_up_for_14_days(db_session: Session, free) -> None:
    _purchase(db_session, free.id, "active", 1)
    assert entitlement_service.addon_access(db_session, organization_id=free.id, addon_key=DOMAIN, now=NOW).state \
        == entitlement_service.STATE_ACTIVE
    domain = _verified_domain(db_session, free.id)

    _purchase(db_session, free.id, "cancelled", 2)
    row = _ledger(db_session, free.id)
    assert row.status == ADDON_STATUS_GRACE
    assert row.grace_ends_at == NOW + timedelta(days=14)
    assert domain.status == DOMAIN_STATUS_VERIFIED, "the customer's users keep reaching the domain during grace"

    later = NOW + timedelta(days=14, minutes=1)
    addon_service.reconcile_organization(db_session, organization_id=free.id, now=later, only=DOMAIN)
    row = _ledger(db_session, free.id)
    assert row.status == ADDON_STATUS_LAPSED and row.halted_at == later
    db_session.refresh(domain)
    assert domain.status == DOMAIN_STATUS_REVOKED, "after grace the hostname is taken offline"
    assert db_session.get(CustomDomain, domain.id) is not None, "the claim is kept"

    _purchase(db_session, free.id, "active", 3, sub="sub_addon_2", at=later + timedelta(days=1))
    assert _ledger(db_session, free.id).status == ADDON_STATUS_ACTIVE
    db_session.refresh(domain)
    assert domain.status == DOMAIN_STATUS_REVOKED, "a lapsed domain is re-verified, never silently resumed"


def test_a_cancellation_with_nothing_built_on_it_lapses_at_once(db_session: Session, free) -> None:
    _purchase(db_session, free.id, "active", 1)
    _purchase(db_session, free.id, "cancelled", 2)
    row = _ledger(db_session, free.id)
    assert row.status == ADDON_STATUS_LAPSED and row.grace_ends_at is None


def test_an_older_gateway_state_cannot_resurrect_a_cancellation(db_session: Session, free) -> None:
    _purchase(db_session, free.id, "active", 1)
    _purchase(db_session, free.id, "cancelled", 3)
    _purchase(db_session, free.id, "active", 2)  # fetched earlier, delivered later
    assert _ledger(db_session, free.id).status == ADDON_STATUS_LAPSED
    assert entitlement_service.addon_access(db_session, organization_id=free.id, addon_key=DOMAIN, now=NOW).state \
        != entitlement_service.STATE_ACTIVE


def test_a_second_live_subscription_is_refused_as_a_duplicate_charge(db_session: Session, free) -> None:
    _purchase(db_session, free.id, "active", 1, sub="sub_first")
    with pytest.raises(addon_service.AddonPurchaseConflictError, match="duplicate"):
        _purchase(db_session, free.id, "active", 2, sub="sub_second")


def test_a_bundled_addon_survives_a_cancelled_purchase_and_a_downgrade_gets_the_same_grace(
    db_session: Session, tenant
) -> None:
    org = tenant.organization
    put_on_plan(db_session, org, "developer")
    _purchase(db_session, org.id, "active", 1)
    domain = _verified_domain(db_session, org.id, "portal.acme-addon.example")
    _purchase(db_session, org.id, "cancelled", 2)
    row = _ledger(db_session, org.id)
    assert (row.status, row.granted_by) == (ADDON_STATUS_ACTIVE, entitlement_service.GRANTED_BY_TIER)

    put_on_plan(db_session, org, "free")
    addon_service.reconcile_organization(db_session, organization_id=org.id, now=NOW, only=DOMAIN)
    assert _ledger(db_session, org.id).status == ADDON_STATUS_GRACE
    assert domain.status == DOMAIN_STATUS_VERIFIED


def test_nobody_is_sold_an_addon_they_already_have(db_session: Session, tenant, monkeypatch) -> None:
    org = tenant.organization
    put_on_plan(db_session, org, "developer")
    with pytest.raises(addon_service.AddonAlreadyGrantedError, match="already included"):
        addon_service.create_addon_checkout(db_session, organization_id=org.id, addon_key=DOMAIN)

    put_on_plan(db_session, org, "free")
    monkeypatch.setattr(entitlement_service, "purchasable", lambda _key: False)
    from app.services.billing import portal_service

    with pytest.raises(portal_service.CheckoutConfigurationError, match="upgrade to a plan that includes it"):
        addon_service.create_addon_checkout(db_session, organization_id=org.id, addon_key=DOMAIN)

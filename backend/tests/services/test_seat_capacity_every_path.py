"""Seats are enforced on every way a person enters an organization (campaign session 1).

    pytest tests/services/test_seat_capacity_every_path.py -q

Before: `organizations.seat_limit` was the only seat ceiling, nothing wrote it, and the
check returned early when it was NULL, so every organization, Free included, could add
unlimited members; on a paid plan the purchased seat quantity was never consulted, and
single sign-on and SCIM provisioning had a seat check that read a tier row named
`seats` that no tier has ever carried.

Now: Free holds the seats its plan declares (`limit.seats`, owner + one); a paid
organization holds the seats its subscription bought. Invitations (issue, resend,
accept), single sign-on and SCIM provisioning and SCIM reactivation all go through one
check, which refuses with a machine-readable reason.
"""

from __future__ import annotations

import datetime
import uuid
from datetime import timedelta, timezone

import pytest
from sqlalchemy.orm import Session

from app.core import security
from app.core.exceptions import SeatLimitExceededError
from app.models.billing_account import BillingAccount
from app.models.identity import EnterpriseIdpConfig, JitProvisioningMode, VerifiedDomain
from app.models.organization import (
    MembershipStatus,
    Organization,
    OrganizationMember,
    OrganizationRole,
    OrganizationStatus,
)
from app.models.price_book import PriceBook
from app.models.subscription import Subscription, SubscriptionStatus
from app.models.user import User
from app.services import organization_invitation_service
from app.services.identity import deprovision_service, jit_service
from app.services.identity.errors import IdentityRefused
from tests.security.plans import put_on_plan


def _utc(**kwargs) -> datetime.datetime:
    return datetime.datetime.now(timezone.utc) + timedelta(**kwargs)


def _user(db: Session, label: str) -> User:
    user = User(
        email=f"{label}-{uuid.uuid4().hex[:8]}@seats.example",
        hashed_password=security.get_password_hash("test-password"),
        is_active=True,
        email_verified_at=_utc(),
    )
    db.add(user)
    db.flush()
    return user


def _member(db: Session, org: Organization, user: User, role=OrganizationRole.MEMBER, status=MembershipStatus.ACTIVE):
    membership = OrganizationMember(organization_id=org.id, user_id=user.id, role=role, status=status)
    db.add(membership)
    db.flush()
    return membership


def _organization(db: Session, plan: str, *, members: int, seats_purchased: int | None = None) -> tuple[Organization, User]:
    org = Organization(slug=f"seats-{uuid.uuid4().hex[:8]}", name="Seats Inc.", status=OrganizationStatus.ACTIVE)
    db.add(org)
    db.flush()
    owner = _user(db, "owner")
    _member(db, org, owner, OrganizationRole.OWNER)
    for index in range(members - 1):
        _member(db, org, _user(db, f"member{index}"))
    tier = put_on_plan(db, org, plan)
    if seats_purchased is not None:
        customer = f"cus_test_{uuid.uuid4().hex[:10]}"
        account = BillingAccount(
            organization_id=org.id, gateway="STRIPE", stripe_customer_id=customer,
            gateway_customer_id=customer, currency="USD", billing_email=owner.email,
        )
        db.add(account)
        db.flush()
        subscription_id = f"sub_test_{uuid.uuid4().hex[:10]}"
        db.add(
            Subscription(
                billing_account_id=account.id, gateway="STRIPE", stripe_subscription_id=subscription_id,
                gateway_subscription_id=subscription_id, status=SubscriptionStatus.ACTIVE,
                quota_tier_key=plan, quota_tier_id=tier.id,
                price_book_id=db.query(PriceBook).first().id, seats_purchased=seats_purchased,
                current_period_start=_utc(days=-1), current_period_end=_utc(days=29),
            )
        )
    db.commit()
    return org, owner


def _invite(db: Session, org: Organization, owner: User, email: str | None = None):
    return organization_invitation_service.create_invitation(
        db,
        organization=org,
        inviter=owner,
        actor_role=OrganizationRole.OWNER,
        email=email or f"invitee-{uuid.uuid4().hex[:8]}@seats.example",
        organization_role=OrganizationRole.MEMBER,
    )


# --- Free: the plan's seats ---------------------------------------------------


def test_free_includes_two_seats_and_refuses_a_third_person(db_session: Session) -> None:
    org, owner = _organization(db_session, "free", members=1)

    _invite(db_session, org, owner)  # owner + one pending invitation = 2 seats
    db_session.commit()

    with pytest.raises(SeatLimitExceededError) as refused:
        _invite(db_session, org, owner)
    details = refused.value.details
    assert details["reason"] == "SEAT_LIMIT_REACHED"
    assert details["plan"] == "free"
    assert details["seat_capacity"] == 2
    assert details["seats_used"] == 2
    assert details["remedy"] == "UPGRADE_PLAN"


# --- Paid: the purchased seat quantity ---------------------------------------


def test_a_paid_organization_holds_the_seats_it_bought(db_session: Session) -> None:
    org, owner = _organization(db_session, "business", members=2, seats_purchased=2)

    with pytest.raises(SeatLimitExceededError) as refused:
        _invite(db_session, org, owner)
    details = refused.value.details
    assert details["source"] == "PURCHASED"
    assert details["seat_capacity"] == 2
    assert details["can_purchase_seats"] is True
    assert details["remedy"] == "BUY_SEATS"


def test_a_paid_organization_with_a_free_seat_can_invite(db_session: Session) -> None:
    org, owner = _organization(db_session, "business", members=2, seats_purchased=3)
    issued = _invite(db_session, org, owner)
    assert issued.invitation.organization_id == org.id


# --- Single sign-on (JIT) and SCIM -------------------------------------------


def _sso(db: Session, org: Organization) -> tuple[EnterpriseIdpConfig, str]:
    domain_name = f"seats-{uuid.uuid4().hex[:8]}.test"
    domain = VerifiedDomain(
        organization_id=org.id, domain=domain_name, status="VERIFIED", challenge_token="tok",
        challenge_expires_at=_utc(days=1), first_verified_at=_utc(days=-1), is_sso_binding=True,
    )
    db.add(domain)
    db.flush()
    config = EnterpriseIdpConfig(
        organization_id=org.id, verified_domain_id=domain.id, protocol="SAML2", display_name="SAML",
        is_active=True, idp_entity_id=f"https://idp.{domain_name}/saml/metadata",
        idp_sso_url=f"https://idp.{domain_name}/saml/sso", jit_default_org_role="MEMBER",
        jit_provisioning_mode=JitProvisioningMode.OPEN,
    )
    db.add(config)
    db.commit()
    return config, domain_name


def test_single_sign_on_does_not_provision_past_the_purchased_seats(db_session: Session) -> None:
    org, _ = _organization(db_session, "enterprise", members=2, seats_purchased=2)
    config, domain = _sso(db_session, org)

    with pytest.raises(IdentityRefused) as refused:
        jit_service.provision_or_link(
            db_session, config=config, external_id="ext-new", email=f"new@{domain}", attributes={},
        )
    assert refused.value.outcome == "REJECTED_SEAT_CAP"


def test_single_sign_on_provisions_into_a_free_seat(db_session: Session) -> None:
    org, _ = _organization(db_session, "enterprise", members=2, seats_purchased=3)
    config, domain = _sso(db_session, org)

    result = jit_service.provision_or_link(
        db_session, config=config, external_id="ext-new", email=f"new@{domain}", attributes={},
    )
    assert result.created_membership


def test_scim_reactivation_needs_a_seat(db_session: Session) -> None:
    org, _ = _organization(db_session, "enterprise", members=2, seats_purchased=2)
    returning = _user(db_session, "returning")
    _member(db_session, org, returning, status=MembershipStatus.DEACTIVATED)
    db_session.commit()

    with pytest.raises(SeatLimitExceededError):
        deprovision_service.reactivate_member(
            db_session, organization_id=org.id, user_id=returning.id, role="MEMBER",
        )

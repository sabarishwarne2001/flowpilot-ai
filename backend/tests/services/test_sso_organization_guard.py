"""Tests for SSO organization enforcement on password vs SSO sessions (ARCH-16)."""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

import pytest
from sqlalchemy.orm import Session

from app.api.deps import _assert_sso_compliance
from app.core.exceptions import OrganizationPermissionDeniedError
from app.models.identity import (
    DomainStatus,
    EnterpriseIdpConfig,
    IdpProtocol,
    TenantSecurityPolicy,
    VerifiedDomain,
)
from app.models.organization import Organization, OrganizationMember, OrganizationRole
from app.models.user import User
from app.models.user_session import AuthMethod, UserSession


@pytest.fixture()
def db(db_session: Session) -> Session:
    return db_session


@pytest.fixture()
def org(db: Session) -> Organization:
    org = Organization(name="SSO Guard Test Org", slug=f"sso-guard-{uuid.uuid4().hex[:8]}")
    db.add(org)
    db.flush([org])
    return org


@pytest.fixture()
def user(db: Session) -> User:
    user = User(
        email=f"sso-user-{uuid.uuid4().hex[:8]}@example.com",
        hashed_password="hash",
        is_active=True,
    )
    if hasattr(User, "email_verified_at"):
        user.email_verified_at = datetime.now(timezone.utc)
    db.add(user)
    db.flush([user])
    return user


def _create_session(
    db: Session,
    user: User,
    method: AuthMethod,
    idp_config_id: Optional[uuid.UUID] = None,
) -> UserSession:
    sess = UserSession(
        user_id=user.id,
        family_id=uuid.uuid4(),
        token_hash=hashlib.sha256(uuid.uuid4().bytes).hexdigest(),
        expires_at=datetime.now(timezone.utc) + timedelta(days=14),
        authenticated_at=datetime.now(timezone.utc),
        auth_method=method,
        idp_config_id=idp_config_id,
    )
    db.add(sess)
    db.flush([sess])
    return sess


def test_password_session_is_rejected_for_sso_required_member(db: Session, org: Organization, user: User):
    policy = TenantSecurityPolicy(
        organization_id=org.id,
        require_sso=True,
        sso_bypass_for_owners=True,
    )
    db.add(policy)

    membership = OrganizationMember(
        organization_id=org.id,
        user_id=user.id,
        role=OrganizationRole.MEMBER,
        status="ACTIVE",
    )
    db.add(membership)
    db.flush()

    sess = _create_session(db, user, AuthMethod.PASSWORD)

    with pytest.raises(OrganizationPermissionDeniedError, match="requires SSO authentication"):
        _assert_sso_compliance(
            db,
            organization_id=org.id,
            membership=membership,
            session_id=sess.id,
        )


def test_password_session_is_allowed_for_sso_owner_bypass(db: Session, org: Organization, user: User):
    policy = TenantSecurityPolicy(
        organization_id=org.id,
        require_sso=True,
        sso_bypass_for_owners=True,
    )
    db.add(policy)

    membership = OrganizationMember(
        organization_id=org.id,
        user_id=user.id,
        role=OrganizationRole.OWNER,
        status="ACTIVE",
    )
    db.add(membership)
    db.flush()

    sess = _create_session(db, user, AuthMethod.PASSWORD)

    # Should not raise error for OWNER when bypass is True
    _assert_sso_compliance(
        db,
        organization_id=org.id,
        membership=membership,
        session_id=sess.id,
    )


def test_sso_session_is_allowed_in_sso_required_organization(db: Session, org: Organization, user: User):
    now = datetime.now(timezone.utc)

    policy = TenantSecurityPolicy(
        organization_id=org.id,
        require_sso=True,
        sso_bypass_for_owners=False,
    )
    db.add(policy)

    domain = VerifiedDomain(
        organization_id=org.id,
        domain=f"domain-{uuid.uuid4().hex[:6]}.com",
        status=DomainStatus.VERIFIED if hasattr(DomainStatus, "VERIFIED") else "VERIFIED",
        challenge_token=uuid.uuid4().hex,
        challenge_expires_at=now + timedelta(days=30),
        first_verified_at=now,
        last_checked_at=now,
        last_seen_at=now,
    )
    db.add(domain)
    db.flush([domain])

    idp = EnterpriseIdpConfig(
        organization_id=org.id,
        verified_domain_id=domain.id,
        protocol=IdpProtocol.SAML2 if hasattr(IdpProtocol, "SAML2") else "SAML2",
        display_name="Enterprise SAML IdP",
        idp_entity_id="https://idp.example.com/metadata",
        idp_sso_url="https://idp.example.com/sso",
        is_active=True,
        jit_provisioning_mode="CAPPED",
        jit_seat_cap=50,
    )
    db.add(idp)
    db.flush([idp])

    membership = OrganizationMember(
        organization_id=org.id,
        user_id=user.id,
        role=OrganizationRole.MEMBER,
        status="ACTIVE",
    )
    db.add(membership)
    db.flush()

    sess = _create_session(db, user, AuthMethod.SAML2, idp_config_id=idp.id)

    # SAML2 session is allowed in SSO-required organization
    _assert_sso_compliance(
        db,
        organization_id=org.id,
        membership=membership,
        session_id=sess.id,
    )


def test_password_session_is_allowed_when_sso_disabled(db: Session, org: Organization, user: User):
    policy = TenantSecurityPolicy(
        organization_id=org.id,
        require_sso=False,
    )
    db.add(policy)

    membership = OrganizationMember(
        organization_id=org.id,
        user_id=user.id,
        role=OrganizationRole.MEMBER,
        status="ACTIVE",
    )
    db.add(membership)
    db.flush()

    sess = _create_session(db, user, AuthMethod.PASSWORD)

    # Password session is allowed when require_sso is False
    _assert_sso_compliance(
        db,
        organization_id=org.id,
        membership=membership,
        session_id=sess.id,
    )
"""N-039 (F-235) — single sign-on took over an unverified account without taking it back.

    pytest tests/services/test_jit_unverified_account.py -q

Just-in-time provisioning links a first single-sign-on login to an existing
FlowPilot account with the same email. If that account's address was never
verified, whoever registered it (not necessarily the mailbox owner) kept its
password, and so a way into the account the real owner now uses through their
identity provider. Decided 2026-10-10 (founder authority): the identity
provider vouches for the address, so the link takes the account over: the
address is verified, the old password stops working and every old session ends.
A verified account is linked as before, untouched.
"""

from __future__ import annotations

import datetime
import uuid
from datetime import timedelta, timezone

from app.core import security
from app.models.identity import EnterpriseIdpConfig, JitProvisioningMode, VerifiedDomain
from app.models.organization import Organization
from app.models.user import User
from app.services.identity import jit_service

SQUATTER_PASSWORD = "someone else's password 9"


def _utc(**kwargs) -> datetime.datetime:
    return datetime.datetime.now(timezone.utc) + timedelta(**kwargs)


def _sso(db) -> tuple[EnterpriseIdpConfig, str]:
    suffix = uuid.uuid4().hex[:8]
    domain_name = f"acme-{suffix}.test"
    org = Organization(name="Acme", slug=f"acme-jit-{suffix}")
    db.add(org)
    db.flush()
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
    db.flush()
    return config, domain_name


def _account(db, email: str, *, verified: bool) -> User:
    user = User(email=email, hashed_password=security.get_password_hash(SQUATTER_PASSWORD), is_active=True,
                email_verified_at=_utc() if verified else None)
    db.add(user)
    db.flush()
    return user


def test_sso_takes_over_an_account_whose_address_was_never_verified(db_session) -> None:
    config, domain = _sso(db_session)
    squatted = _account(db_session, f"ceo@{domain}", verified=False)

    result = jit_service.provision_or_link(
        db_session, config=config, external_id="ext-ceo", email=f"ceo@{domain}", attributes={},
    )

    assert result.user_id == squatted.id
    db_session.expire_all()
    user = db_session.get(User, squatted.id)
    assert user.email_verified_at is not None
    assert not security.verify_password(SQUATTER_PASSWORD, user.hashed_password)
    assert user.sessions_revoked_at is not None


def test_sso_links_a_verified_account_untouched(db_session) -> None:
    config, domain = _sso(db_session)
    owner = _account(db_session, f"cfo@{domain}", verified=True)

    result = jit_service.provision_or_link(
        db_session, config=config, external_id="ext-cfo", email=f"cfo@{domain}", attributes={},
    )

    assert result.user_id == owner.id
    db_session.expire_all()
    user = db_session.get(User, owner.id)
    assert security.verify_password(SQUATTER_PASSWORD, user.hashed_password)
    assert user.sessions_revoked_at is None

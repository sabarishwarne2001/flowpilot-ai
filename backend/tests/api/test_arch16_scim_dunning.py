"""S1 — SCIM under dunning read-only degradation."""

from __future__ import annotations

import uuid
from datetime import timedelta
import pytest
from app.core import security
from app.models.user import User
from app.models.organization import Organization
from app.models.identity import EnterpriseIdpConfig, DirectoryIdentity, VerifiedDomain, JitProvisioningMode
from app.services.identity import scim_service


@pytest.fixture
def identity_setup(db_session):
    db = db_session
    user_uid = uuid.uuid4()
    test_user = User(
        id=user_uid,
        email=f"scim-test-{user_uid.hex[:8]}@acme.test",
        hashed_password=security.get_password_hash("password123"),
        is_active=True,
    )
    db.add(test_user)
    db.flush()

    org = Organization(name="Scim Dunning Org", slug=f"scim-dunning-{user_uid.hex[:6]}")
    db.add(org)
    db.flush()

    now = scim_service.utcnow()
    domain = VerifiedDomain(
        organization_id=org.id,
        domain="acme.test",
        status="VERIFIED",
        challenge_token="token",
        challenge_issued_at=now,
        challenge_expires_at=now + timedelta(days=30),
        first_verified_at=now,
        is_sso_binding=True,
    )
    db.add(domain)
    db.flush()

    config = EnterpriseIdpConfig(
        organization_id=org.id,
        verified_domain_id=domain.id,
        protocol="SAML2",
        display_name="Acme SAML",
        is_active=True,
        idp_entity_id="https://idp.acme.test/saml/metadata",
        idp_sso_url="https://idp.acme.test/saml/sso",
        jit_provisioning_mode=JitProvisioningMode.CAPPED,
        jit_seat_cap=25,
    )
    db.add(config)
    db.flush()

    key_row, raw_token = scim_service.issue_key(
        db, organization_id=org.id, idp_config_id=config.id, display_name="Test SCIM"
    )

    ident = DirectoryIdentity(
        organization_id=org.id,
        idp_config_id=config.id,
        user_id=test_user.id,
        external_id="ext-user-1",
        user_name=test_user.email,
        active=True,
        provisioned_via="SCIM",
    )
    db.add(ident)
    db.flush()
    db.commit()

    return {
        "org": org,
        "config": config,
        "token": raw_token,
        "identity": ident,
        "key": key_row,
    }


def test_scim_routes_are_on_the_public_register():
    from app.core.public_route_registry import is_public
    assert is_public("/scim/v2/Users", "GET")
    assert is_public("/api/v1/saml/acs", "POST")


def test_scim_requires_a_token(client, identity_setup):
    ident = identity_setup["identity"]
    response = client.get(f"/scim/v2/Users/{ident.id}")
    assert response.status_code in (401, 404)


def test_scim_content_type_is_scim_json(client, identity_setup):
    token = identity_setup["token"]
    ident = identity_setup["identity"]
    response = client.get(
        f"/scim/v2/Users/{ident.id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/scim+json")

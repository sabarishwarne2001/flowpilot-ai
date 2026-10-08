"""Requiring single sign-on needs an identity provider to sign in with (F-188).

Ticking "Require single sign-on" on the identity console's Security tab was accepted with no
identity provider connected. From then on every request a member made into the organization with
a password session was refused ("This organization requires SSO authentication"), and there was
no SSO to sign in with: the whole membership was locked out, and with "Owners can bypass SSO" off
the owners too, with no self-service way back. The server now refuses to require SSO until an
active identity provider exists; turning the requirement off is always allowed.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.models.identity import DomainStatus, EnterpriseIdpConfig, IdpProtocol, VerifiedDomain
from tests.engines.conftest import Engines

POLICY = "/identity/security-policy"


def _active_idp(engines: Engines) -> None:
    domain = VerifiedDomain(
        organization_id=engines.org,
        domain="sso-e2e.example.com",
        status=DomainStatus.VERIFIED,
        challenge_token="f188-challenge",
        challenge_expires_at=datetime.now(timezone.utc) + timedelta(days=1),
        first_verified_at=datetime.now(timezone.utc),
        last_seen_at=datetime.now(timezone.utc),
        is_sso_binding=True,
    )
    engines.db.add(domain)
    engines.db.flush()
    engines.db.add(
        EnterpriseIdpConfig(
            organization_id=engines.org,
            verified_domain_id=domain.id,
            protocol=IdpProtocol.SAML2,
            display_name="Okta (F-188)",
            is_active=True,
            idp_entity_id="http://www.okta.com/f188",
            idp_sso_url="https://example.okta.com/app/f188/sso/saml",
            jit_seat_cap=10,
        )
    )
    engines.db.commit()


def test_sso_cannot_be_required_without_an_active_identity_provider(engines: Engines) -> None:
    engines.plan("enterprise")

    response = engines.put(POLICY, {"require_sso": True}, org=True)

    assert response.status_code == 409, response.text
    assert "identity provider" in response.json()["detail"].lower()
    assert engines.get(POLICY, org=True).json()["require_sso"] is False
    # The members are not locked out.
    member = engines.get("/work-items", as_user=engines.tenant.contributor)
    assert member.status_code == 200, member.text


def test_sso_can_be_required_once_an_identity_provider_is_active_and_always_turned_off(engines: Engines) -> None:
    engines.plan("enterprise")
    _active_idp(engines)

    on = engines.put(POLICY, {"require_sso": True}, org=True)
    assert on.status_code == 200, on.text
    assert on.json()["require_sso"] is True

    off = engines.put(POLICY, {"require_sso": False}, org=True)
    assert off.status_code == 200, off.text
    assert off.json()["require_sso"] is False

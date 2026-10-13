"""Requiring single sign-on is enforced on every request into the organization (campaign session 3).

The identity console's Security tab says "Members must sign in through your identity provider.
Passwords stop working" and, before it saves, "Members signed in with a password lose access to
this organization". Neither was true: the only check (`get_sso_compliant_organization_context`)
was used by no route, so a member who signed in with a password kept full access to every
organization and workspace route after the switch was on. The one place that did read the switch
was accepting an invitation.

Now every organization- and workspace-scoped request checks the organization's policy: a member
whose role SSO applies to must be on a session that came from THIS organization's identity
provider (a SAML or OIDC session from another organization's provider does not count), owners
pass with break-glass on, and API keys are not people and are not asked.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from app.core import security
from app.models.identity import (
    DomainStatus,
    EnterpriseIdpConfig,
    IdpProtocol,
    TenantSecurityPolicy,
    VerifiedDomain,
)
from app.models.organization import Organization
from app.services import session_service
from tests.engines.conftest import Engines


def _idp(engines: Engines, organization_id: uuid.UUID, domain: str) -> EnterpriseIdpConfig:
    now = datetime.now(timezone.utc)
    verified = VerifiedDomain(
        organization_id=organization_id,
        domain=domain,
        status=DomainStatus.VERIFIED,
        challenge_token=f"challenge-{uuid.uuid4().hex[:8]}",
        challenge_expires_at=now + timedelta(days=1),
        first_verified_at=now,
        last_seen_at=now,
        is_sso_binding=True,
    )
    engines.db.add(verified)
    engines.db.flush()
    config = EnterpriseIdpConfig(
        organization_id=organization_id,
        verified_domain_id=verified.id,
        protocol=IdpProtocol.SAML2,
        display_name=f"IdP for {domain}",
        is_active=True,
        idp_entity_id=f"http://idp.example/{domain}",
        idp_sso_url=f"https://idp.example/{domain}/sso",
        jit_seat_cap=10,
    )
    engines.db.add(config)
    engines.db.flush()
    return config


def _require_sso(engines: Engines, *, bypass_for_owners: bool = True) -> EnterpriseIdpConfig:
    engines.plan("enterprise")
    config = _idp(engines, engines.org, f"sso-{uuid.uuid4().hex[:6]}.example.com")
    engines.db.commit()
    response = engines.put("/identity/security-policy", {"require_sso": True}, org=True)
    assert response.status_code == 200, response.text
    if not bypass_for_owners:
        response = engines.put(
            "/identity/security-policy", {"sso_bypass_for_owners": False}, org=True
        )
        assert response.status_code == 200, response.text
    return config


def _signed_in(engines: Engines, user, *, method: str = "PASSWORD", idp_config_id=None) -> dict[str, str]:
    """Headers for a real session row, as /auth/login or the SSO callback would open it."""
    issued = session_service.create_session(
        engines.db, user=user, auth_method=method, idp_config_id=idp_config_id
    )
    engines.db.commit()
    token = security.create_access_token(subject=str(user.id), session_id=issued.session.id)
    return {"Authorization": f"Bearer {token}"}


def _call(engines: Engines, path: str, headers: dict[str, str]):
    return engines.client.get(path, headers=headers)


def test_a_password_session_loses_the_organization_once_sso_is_required(engines: Engines) -> None:
    contributor = engines.tenant.contributor.user
    headers = _signed_in(engines, contributor)
    assert _call(engines, engines.url("/work-items"), headers).status_code == 200

    _require_sso(engines)

    for path in (engines.url("/work-items"), engines.org_url("/members")):
        refused = _call(engines, path, headers)
        assert refused.status_code == 403, (path, refused.text)
        body = refused.json()
        assert body["code"] == "ORGANIZATION_SSO_REQUIRED", body


def test_a_session_from_this_organizations_identity_provider_keeps_access(engines: Engines) -> None:
    config = _require_sso(engines)
    headers = _signed_in(
        engines, engines.tenant.contributor.user, method="SAML2", idp_config_id=config.id
    )
    assert _call(engines, engines.url("/work-items"), headers).status_code == 200
    assert _call(engines, engines.org_url("/members"), headers).status_code == 200


def test_a_session_from_another_organizations_identity_provider_does_not_count(engines: Engines) -> None:
    _require_sso(engines)
    other = Organization(name="Elsewhere Ltd", slug=f"elsewhere-{uuid.uuid4().hex[:6]}")
    engines.db.add(other)
    engines.db.flush()
    foreign_idp = _idp(engines, other.id, f"elsewhere-{uuid.uuid4().hex[:6]}.example.com")
    engines.db.commit()

    headers = _signed_in(
        engines, engines.tenant.contributor.user, method="SAML2", idp_config_id=foreign_idp.id
    )
    refused = _call(engines, engines.url("/work-items"), headers)
    assert refused.status_code == 403, refused.text
    assert refused.json()["code"] == "ORGANIZATION_SSO_REQUIRED"


def test_owners_keep_password_access_only_while_break_glass_is_on(engines: Engines) -> None:
    owner = engines.tenant.owner.user
    _require_sso(engines)
    headers = _signed_in(engines, owner)
    assert _call(engines, engines.org_url("/members"), headers).status_code == 200

    policy = engines.db.query(TenantSecurityPolicy).filter_by(organization_id=engines.org).one()
    policy.sso_bypass_for_owners = False
    engines.db.commit()

    refused = _call(engines, engines.org_url("/members"), headers)
    assert refused.status_code == 403, refused.text
    assert refused.json()["code"] == "ORGANIZATION_SSO_REQUIRED"


def test_an_organization_without_the_requirement_is_unaffected(engines: Engines) -> None:
    engines.plan("enterprise")
    headers = _signed_in(engines, engines.tenant.contributor.user)
    assert _call(engines, engines.url("/work-items"), headers).status_code == 200
    assert _call(engines, engines.org_url("/members"), headers).status_code == 200

"""An organization can require two-factor sign-in (campaign session 3; N-017 decided again).

Enterprise buyers ask for it first. An owner turns it on for the organization (every plan: it is a
security baseline, as GitHub offers it free). From then on a person who signs in with a password
and has no second factor is refused everywhere in the organization (403
`ORGANIZATION_MFA_REQUIRED`) until they turn it on, which they can always reach (`/me/mfa`); a
session from the organization's own identity provider counts (the provider is where its MFA
lives); API keys are not people. Turning it on needs two-factor on the owner's own account, and
nobody can switch off their second factor while an organization they belong to requires it.
"""

from __future__ import annotations

from app.core import security, totp
from app.services import mfa_service, session_service
from tests.engines.conftest import Engines
from tests.engines.saml_idp import TestIdp

POLICY = "/two-factor-policy"


def _session(engines: Engines, user, *, method: str = "PASSWORD", idp_config_id=None) -> dict[str, str]:
    issued = session_service.create_session(engines.db, user=user, auth_method=method, idp_config_id=idp_config_id)
    engines.db.commit()
    return {"Authorization": f"Bearer {security.create_access_token(subject=str(user.id), session_id=issued.session.id)}"}


def _enrol(engines: Engines, user) -> str:
    enrolment = mfa_service.start(engines.db, user=user)
    mfa_service.confirm(engines.db, user=user, code=totp.code_at(enrolment.secret))
    engines.db.commit()
    return enrolment.secret


def _require(engines: Engines, owner_headers) -> None:
    response = engines.client.put(engines.org_url(POLICY), json={"required": True}, headers=owner_headers)
    assert response.status_code == 200, response.text


def test_an_owner_without_two_factor_cannot_require_it(engines: Engines) -> None:
    owner = _session(engines, engines.tenant.owner.user)
    refused = engines.client.put(engines.org_url(POLICY), json={"required": True}, headers=owner)
    assert refused.status_code == 409, refused.text
    assert "your own account" in refused.json()["detail"].lower()


def test_members_without_a_second_factor_are_refused_until_they_turn_it_on(engines: Engines) -> None:
    owner_user = engines.tenant.owner.user
    _enrol(engines, owner_user)
    owner = _session(engines, owner_user)
    _require(engines, owner)

    state = engines.client.get(engines.org_url(POLICY), headers=owner).json()
    assert state["required"] is True
    without = {m["email"] for m in state["members_without_two_factor"]}
    assert engines.tenant.contributor.user.email in without
    assert owner_user.email not in without

    member_user = engines.tenant.contributor.user
    member = _session(engines, member_user)
    for path in (engines.url("/work-items"), engines.org_url("/members")):
        refused = engines.client.get(path, headers=member)
        assert refused.status_code == 403, (path, refused.text)
        assert refused.json()["code"] == "ORGANIZATION_MFA_REQUIRED"
    # The way to fix it is always reachable.
    assert engines.client.get("/api/v1/me/mfa", headers=member).status_code == 200

    _enrol(engines, member_user)
    assert engines.client.get(engines.url("/work-items"), headers=member).status_code == 200


def test_a_session_from_the_organizations_identity_provider_counts(engines: Engines) -> None:
    engines.plan("enterprise")
    _enrol(engines, engines.tenant.owner.user)
    _require(engines, _session(engines, engines.tenant.owner.user))
    provider = TestIdp.create()
    provider.install(engines.db, engines.org)
    sso = _session(engines, engines.tenant.contributor.user, method="SAML2", idp_config_id=provider.config.id)
    assert engines.client.get(engines.url("/work-items"), headers=sso).status_code == 200


def test_nobody_turns_off_their_second_factor_while_an_organization_requires_it(engines: Engines) -> None:
    owner_user = engines.tenant.owner.user
    _enrol(engines, owner_user)
    owner = _session(engines, owner_user)
    _require(engines, owner)
    secret = _enrol(engines, engines.tenant.contributor.user)
    member = _session(engines, engines.tenant.contributor.user)
    refused = engines.client.post("/api/v1/me/mfa/disable", json={"password": "test-password", "code": totp.code_at(secret, None)}, headers=member)
    assert refused.status_code == 409, refused.text
    assert engines.tenant.organization.name in refused.json()["detail"]


def test_only_owners_change_it_and_it_is_on_every_plan(engines: Engines) -> None:
    engines.plan("free")
    admin_user = engines.tenant.org_admin.user if hasattr(engines.tenant, "org_admin") else None
    if admin_user is not None:
        _enrol(engines, admin_user)
        admin = _session(engines, admin_user)
        assert engines.client.put(engines.org_url(POLICY), json={"required": True}, headers=admin).status_code == 403
    _enrol(engines, engines.tenant.owner.user)
    owner = _session(engines, engines.tenant.owner.user)
    response = engines.client.put(engines.org_url(POLICY), json={"required": True}, headers=owner)
    assert response.status_code == 200, response.text
    off = engines.client.put(engines.org_url(POLICY), json={"required": False}, headers=owner)
    assert off.status_code == 200 and off.json()["required"] is False

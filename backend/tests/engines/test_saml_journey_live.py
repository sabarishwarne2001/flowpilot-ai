"""Single sign-on through SAML, end to end over HTTP, against a self-signed identity provider.

Campaign session 3. The five public SSO routes (`/saml/metadata`, `/sso/discover`, `/sso/start`,
`/saml/acs`, and the OIDC callback) had never been driven as HTTP: the ARCH-28 rig proves the
verifier as a function and F-205 the logout. Here a person signs in the way a browser does:
discovery by email domain, the redirect to the provider with a deflated AuthnRequest, the
provider's signed answer posted to the ACS, the landing redirect with the refresh cookie, and
the cookie exchanged for an access token. Then every way an attacker would try the ACS: a replay,
a forged signature, an unsolicited or expired answer, another organization's provider, and an
open redirect through `redirect_path`.
"""

from __future__ import annotations

import uuid
from urllib.parse import parse_qs, urlparse

import pytest

from app.services.identity._integration import get_settings
from app.core.cookies import REFRESH_COOKIE_NAME
from app.models.identity import JitProvisioningMode
from app.models.organization import Organization
from app.models.user_session import UserSession
from app.models.user import User
from tests.engines.conftest import Engines
from tests.engines.saml_idp import TestIdp, keypair, request_id_from_redirect

API = "/api/v1"


def _sp() -> tuple[str, str]:
    settings = get_settings()
    base = settings.public_api_base
    entity = settings.SAML_SP_ENTITY_ID or f"{base}{settings.API_V1_STR}/saml/metadata"
    return entity, f"{base}{settings.API_V1_STR}/saml/acs"


@pytest.fixture
def idp(engines: Engines) -> TestIdp:
    engines.plan("enterprise")
    provider = TestIdp.create()
    provider.install(engines.db, engines.org, jit_provisioning_mode=JitProvisioningMode.OPEN)
    return provider


def _start(engines: Engines, idp: TestIdp, redirect_path: str = "/workspaces") -> str:
    response = engines.client.get(
        f"{API}/sso/start",
        params={"domain": idp.domain, "redirect_path": redirect_path},
        follow_redirects=False,
    )
    assert response.status_code == 302, response.text
    location = response.headers["location"]
    assert location.startswith(idp.sso_url + "?"), location
    return request_id_from_redirect(location)


def _post_acs(engines: Engines, saml_response: str):
    engines.client.cookies.clear()
    return engines.client.post(
        f"{API}/saml/acs", data={"SAMLResponse": saml_response}, follow_redirects=False
    )


def _sign_in(engines: Engines, idp: TestIdp, email: str, redirect_path: str = "/workspaces"):
    entity, acs = _sp()
    request_id = _start(engines, idp, redirect_path)
    answer = idp.response(email=email, acs_url=acs, sp_entity_id=entity, in_response_to=request_id,
                          attributes={"displayName": ["Sam Single-Sign-On"]})
    return _post_acs(engines, answer), answer


def test_the_service_provider_metadata_names_this_deployment(engines: Engines) -> None:
    response = engines.client.get(f"{API}/saml/metadata")
    assert response.status_code == 200
    assert "samlmetadata+xml" in response.headers["content-type"]
    entity, acs = _sp()
    assert entity in response.text and acs in response.text


def test_discovery_answers_for_a_bound_domain_only(engines: Engines, idp: TestIdp) -> None:
    bound = engines.client.get(f"{API}/sso/discover", params={"domain": idp.domain}).json()
    assert bound["sso_enabled"] is True
    assert bound["start_url"] == f"/api/v1/sso/start?domain={idp.domain}"
    other = engines.client.get(f"{API}/sso/discover", params={"domain": "nobody-here.example.org"}).json()
    assert other == {"sso_enabled": False}


def test_a_person_signs_in_through_the_identity_provider(engines: Engines, idp: TestIdp) -> None:
    email = f"sam-{uuid.uuid4().hex[:6]}@{idp.domain}"
    landed, _ = _sign_in(engines, idp, email)
    assert landed.status_code in (302, 303), landed.text
    location = urlparse(landed.headers["location"])
    assert location.path == "/auth/sso/complete"
    assert parse_qs(location.query).get("next") == ["/workspaces"]
    assert "token" not in landed.headers["location"].lower()  # the credential travels in the cookie only
    cookie = landed.cookies.get(REFRESH_COOKIE_NAME)
    assert cookie, landed.headers

    engines.client.cookies.set(REFRESH_COOKIE_NAME, cookie, path=f"{API}/auth")
    refreshed = engines.client.post(f"{API}/auth/refresh")
    assert refreshed.status_code == 200, refreshed.text
    token = refreshed.json()["access_token"]
    me = engines.client.get(f"{API}/me/profile", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200 and me.json()["email"] == email

    user = engines.db.query(User).filter_by(email=email).one()
    # The ACS opened the session; the refresh rotated it (a new row in the same family).
    sessions = engines.db.query(UserSession).filter_by(user_id=user.id).all()
    assert sessions and len({s.family_id for s in sessions}) == 1
    for session in sessions:
        assert str(getattr(session.auth_method, "value", session.auth_method)) == "SAML2"
        assert session.idp_config_id == idp.config.id
    # The member can work in the organization the provider belongs to.
    assert engines.client.get(engines.org_url("/members"), headers={"Authorization": f"Bearer {token}"}).status_code == 200


def test_a_replayed_answer_is_refused(engines: Engines, idp: TestIdp) -> None:
    landed, answer = _sign_in(engines, idp, f"replay-{uuid.uuid4().hex[:6]}@{idp.domain}")
    assert landed.status_code in (302, 303)
    again = _post_acs(engines, answer)
    assert again.status_code == 401, again.text
    assert REFRESH_COOKIE_NAME not in again.cookies


def test_an_answer_signed_with_another_key_is_refused(engines: Engines, idp: TestIdp) -> None:
    entity, acs = _sp()
    request_id = _start(engines, idp)
    key, cert = keypair("attacker.example.com")
    forged = idp.response(email=f"forged@{idp.domain}", acs_url=acs, sp_entity_id=entity,
                          in_response_to=request_id, key=key, cert=cert)
    response = _post_acs(engines, forged)
    assert response.status_code == 401, response.text
    assert engines.db.query(User).filter_by(email=f"forged@{idp.domain}").one_or_none() is None


def test_an_unsolicited_answer_is_refused_unless_allowed(engines: Engines, idp: TestIdp) -> None:
    entity, acs = _sp()
    answer = idp.response(email=f"unsolicited@{idp.domain}", acs_url=acs, sp_entity_id=entity,
                          in_response_to=None)
    assert _post_acs(engines, answer).status_code == 401


def test_an_expired_answer_is_refused(engines: Engines, idp: TestIdp) -> None:
    entity, acs = _sp()
    request_id = _start(engines, idp)
    stale = idp.response(email=f"stale@{idp.domain}", acs_url=acs, sp_entity_id=entity,
                         in_response_to=request_id, not_on_or_after=-600)
    assert _post_acs(engines, stale).status_code == 401


def test_another_organizations_provider_cannot_sign_people_in_here(engines: Engines, idp: TestIdp) -> None:
    other = Organization(name="Elsewhere Ltd", slug=f"elsewhere-{uuid.uuid4().hex[:6]}")
    engines.db.add(other)
    engines.db.flush()
    foreign = TestIdp.create()
    foreign.install(engines.db, other.id, jit_provisioning_mode=JitProvisioningMode.OPEN)
    entity, acs = _sp()
    request_id = _start(engines, idp)
    # The foreign provider answers our request, naming our provider as issuer, with its own key.
    answer = foreign.response(email=f"intruder@{idp.domain}", acs_url=acs, sp_entity_id=entity,
                              in_response_to=request_id, issuer=idp.entity_id)
    assert _post_acs(engines, answer).status_code == 401
    assert engines.db.query(User).filter_by(email=f"intruder@{idp.domain}").one_or_none() is None


@pytest.mark.parametrize("unsafe", ["//evil.example.com/x", "https://evil.example.com", "/\\evil.example.com"])
def test_redirect_path_cannot_send_the_browser_elsewhere(engines: Engines, idp: TestIdp, unsafe: str) -> None:
    landed, _ = _sign_in(engines, idp, f"redir-{uuid.uuid4().hex[:6]}@{idp.domain}", redirect_path=unsafe)
    assert landed.status_code in (302, 303), landed.text
    location = urlparse(landed.headers["location"])
    assert location.netloc in ("", urlparse(get_settings().FRONTEND_URL).netloc)
    assert parse_qs(location.query).get("next", ["/"]) == ["/"]

"""The identity console's setup, as an owner does it, over HTTP (campaign session 3).

Claim a domain, prove it with a DNS TXT record, bind single sign-on to it, create a SAML
connection, add the provider's signing certificate, map a group to a role and dry-run it,
activate, and then a person signs in through the provider just configured. Issue, rotate and
revoke a SCIM token. Then what an owner gets wrong or an attacker tries: a consumer mail domain,
a wrong TXT record, a certificate that is not a certificate, a metadata or discovery URL that
points inside the network, an identity provider granting OWNER.

DNS is the one stand-in: `dns_service.lookup_txt` answers with the records the test publishes.
"""

from __future__ import annotations

import uuid

import pytest

from app.services.identity import dns_service
from tests.engines.conftest import Engines
from tests.engines.saml_idp import TestIdp, request_id_from_redirect
from tests.engines.test_saml_journey_live import _post_acs, _sp

ID = "/identity"


@pytest.fixture
def published(monkeypatch) -> dict[str, list[str]]:
    """TXT records the 'DNS' answers with, by domain (apex and _servabo.<domain>)."""
    records: dict[str, list[str]] = {}

    def lookup_txt(domain: str, *, subdomain: str | None = None):
        name = f"{subdomain}.{domain}" if subdomain else domain
        return dns_service.TxtLookupResult(domain=name, records=list(records.get(name, [])), resolved=True)

    monkeypatch.setattr(dns_service, "lookup_txt", lookup_txt)
    return records


def _claim_and_verify(engines: Engines, published, domain: str) -> str:
    claimed = engines.post(f"{ID}/domains", {"domain": domain}, org=True)
    assert claimed.status_code == 201, claimed.text
    published[domain] = [claimed.json()["expected_txt_record"]]
    verified = engines.post(f"{ID}/domains/{claimed.json()['id']}/verify", org=True)
    assert verified.status_code == 200, verified.text
    assert verified.json()["status"] == "VERIFIED"
    return claimed.json()["id"]


def test_an_owner_sets_up_saml_and_a_person_signs_in(engines: Engines, published) -> None:
    engines.plan("enterprise")
    provider = TestIdp.create()
    domain_id = _claim_and_verify(engines, published, provider.domain)
    assert engines.post(f"{ID}/domains/{domain_id}/bind-sso", org=True).status_code == 200

    created = engines.post(f"{ID}/idp-configs", {
        "verified_domain_id": domain_id, "protocol": "SAML2", "display_name": "Okta",
        "idp_entity_id": provider.entity_id, "idp_sso_url": provider.sso_url,
        "jit_provisioning_mode": "OPEN",
    }, org=True)
    assert created.status_code == 201, created.text
    config_id = created.json()["id"]

    # Activation needs the provider's certificate first.
    assert engines.post(f"{ID}/idp-configs/{config_id}/activate", org=True).status_code == 409
    cert = engines.post(f"{ID}/idp-configs/{config_id}/certificates",
                        {"certificate_pem": provider.cert, "side": "IDP", "is_primary": True}, org=True)
    assert cert.status_code == 201, cert.text

    mapping = engines.post(f"{ID}/idp-configs/{config_id}/role-mappings", {
        "attribute_name": "groups", "match_kind": "EQUALS", "match_value": "fp-admins",
        "organization_role": "ADMIN"}, org=True)
    assert mapping.status_code == 201, mapping.text
    dry = engines.post(f"{ID}/idp-configs/{config_id}/dry-run", {"attributes": {"groups": ["fp-admins"]}}, org=True)
    assert dry.status_code == 200 and dry.json()["resolved_role"] == "ADMIN"
    plain = engines.post(f"{ID}/idp-configs/{config_id}/dry-run", {"attributes": {"groups": ["staff"]}}, org=True)
    assert plain.json()["resolved_role"] == "MEMBER"

    active = engines.post(f"{ID}/idp-configs/{config_id}/activate", org=True)
    assert active.status_code == 200, active.text

    start = engines.client.get("/api/v1/sso/start", params={"domain": provider.domain}, follow_redirects=False)
    assert start.status_code == 302
    entity, acs = _sp()
    email = f"ada-{uuid.uuid4().hex[:6]}@{provider.domain}"
    answer = provider.response(email=email, acs_url=acs, sp_entity_id=entity,
                               in_response_to=request_id_from_redirect(start.headers["location"]),
                               attributes={"groups": ["fp-admins"]})
    landed = _post_acs(engines, answer)
    assert landed.status_code in (302, 303), landed.text
    members = engines.get("/members", org=True).json()
    rows = members.get("items", members) if isinstance(members, dict) else members
    joined = next(m for m in rows if (m.get("email") or m.get("user", {}).get("email")) == email)
    assert str(joined.get("role")).upper().endswith("ADMIN")


def test_scim_tokens_are_shown_once_rotated_and_revoked(engines: Engines, published) -> None:
    engines.plan("enterprise")
    provider = TestIdp.create()
    provider.install(engines.db, engines.org)
    issued = engines.post(f"{ID}/scim-keys", {"idp_config_id": str(provider.config.id), "display_name": "Okta"}, org=True)
    assert issued.status_code == 201, issued.text
    first = issued.json()["token"]
    key_id = issued.json()["id"]
    listed = engines.get(f"{ID}/scim-keys", org=True)
    assert listed.status_code == 200 and first not in listed.text  # the secret is never shown again
    scim = lambda token: engines.client.get("/scim/v2/Users", headers={"Authorization": f"Bearer {token}"})
    assert scim(first).status_code == 200

    rotated = engines.post(f"{ID}/scim-keys/{key_id}/rotate", org=True)
    assert rotated.status_code == 200, rotated.text
    second = rotated.json()["token"]
    assert second != first
    assert scim(second).status_code == 200
    assert scim(first).status_code == 200  # the old secret works during the overlap window

    assert engines.delete(f"{ID}/scim-keys/{key_id}", org=True).status_code == 204
    assert scim(second).status_code == 404
    assert scim(first).status_code == 404


def test_a_consumer_mail_domain_cannot_be_claimed(engines: Engines, published) -> None:
    engines.plan("enterprise")
    for domain in ("gmail.com", "outlook.com", "com", "co.uk"):
        response = engines.post(f"{ID}/domains", {"domain": domain}, org=True)
        assert response.status_code in (409, 422), (domain, response.status_code, response.text)


def test_a_wrong_txt_record_does_not_verify(engines: Engines, published) -> None:
    engines.plan("enterprise")
    domain = f"wrong-{uuid.uuid4().hex[:6]}.example.com"
    claimed = engines.post(f"{ID}/domains", {"domain": domain}, org=True).json()
    published[domain] = ["servabo-verification=not-the-token"]
    response = engines.post(f"{ID}/domains/{claimed['id']}/verify", org=True)
    assert response.status_code == 409, response.text
    listed = engines.get(f"{ID}/domains", org=True).json()
    assert next(d for d in listed if d["id"] == claimed["id"])["status"] == "PENDING"


def test_a_certificate_that_is_not_a_certificate_is_refused(engines: Engines, published) -> None:
    engines.plan("enterprise")
    provider = TestIdp.create()
    provider.install(engines.db, engines.org)
    for pem in ("-----BEGIN CERTIFICATE-----\nAAAA\n-----END CERTIFICATE-----",
                "-----BEGIN CERTIFICATE-----\nnot base64 at all!\n-----END CERTIFICATE-----"):
        response = engines.post(f"{ID}/idp-configs/{provider.config.id}/certificates",
                                {"certificate_pem": pem, "side": "IDP"}, org=True)
        assert response.status_code == 422, (pem, response.status_code, response.text)


@pytest.mark.parametrize("field,url", [
    ("metadata_url", "http://169.254.169.254/latest/meta-data/"),
    ("metadata_url", "http://127.0.0.1:8000/api/v1/health"),
    ("metadata_url", "https://169.254.169.254/latest/meta-data/"),
    ("metadata_url", "https://127.0.0.1/metadata"),
    ("oidc_discovery_url", "http://10.0.0.5/.well-known/openid-configuration"),
    ("oidc_discovery_url", "https://10.0.0.5/.well-known/openid-configuration"),
    ("oidc_discovery_url", "https://localhost/.well-known/openid-configuration"),
    ("oidc_discovery_url", "https://[::1]/.well-known/openid-configuration"),
])
def test_a_metadata_or_discovery_url_inside_the_network_is_refused(
    engines: Engines, published, field: str, url: str
) -> None:
    engines.plan("enterprise")
    provider = TestIdp.create()
    domain_id = _claim_and_verify(engines, published, provider.domain)
    engines.post(f"{ID}/domains/{domain_id}/bind-sso", org=True)
    body = {"verified_domain_id": domain_id, "protocol": "SAML2" if field == "metadata_url" else "OIDC",
            field: url, "oidc_client_id": "x"}
    response = engines.post(f"{ID}/idp-configs", body, org=True)
    # Refused before anything is fetched or stored (400 or 422, never a 5xx or a 201).
    assert response.status_code in (400, 422), (url, response.status_code, response.text)


def test_saml_and_oidc_refuse_an_unusable_address_the_same_way(engines: Engines, published) -> None:
    """F-371 (campaign session 4): an address the server cannot use is invalid configuration input.

    SAML's metadata URL answered 422 with the reason; OIDC's discovery URL answered 400 (the
    identity error's default) for the same mistake, in a different body.
    """
    engines.plan("enterprise")
    provider = TestIdp.create()
    domain_id = _claim_and_verify(engines, published, provider.domain)
    engines.post(f"{ID}/domains/{domain_id}/bind-sso", org=True)
    saml = engines.post(f"{ID}/idp-configs", {
        "verified_domain_id": domain_id, "protocol": "SAML2", "metadata_url": "https://127.0.0.1/metadata",
    }, org=True)
    oidc = engines.post(f"{ID}/idp-configs", {
        "verified_domain_id": domain_id, "protocol": "OIDC", "oidc_client_id": "x",
        "oidc_discovery_url": "https://127.0.0.1/.well-known/openid-configuration",
    }, org=True)
    assert (saml.status_code, oidc.status_code) == (422, 422), (saml.text, oidc.text)
    assert sorted(saml.json()) == sorted(oidc.json()), (saml.json(), oidc.json())
    assert "discovery" in str(oidc.json()["detail"]).lower(), oidc.text


def test_an_identity_provider_cannot_grant_owner(engines: Engines, published) -> None:
    engines.plan("enterprise")
    provider = TestIdp.create()
    provider.install(engines.db, engines.org)
    response = engines.post(f"{ID}/idp-configs/{provider.config.id}/role-mappings", {
        "attribute_name": "groups", "match_value": "everyone", "organization_role": "OWNER"}, org=True)
    assert response.status_code == 422

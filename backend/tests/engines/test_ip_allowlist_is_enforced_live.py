"""An organization's IP allowlist is enforced (campaign session 3).

`PUT /organizations/{id}/identity/security-policy` accepted `ip_allowlist` (a list of networks),
stored it and returned it, and nothing ever read it: `session_policy_service.ip_allowed` had no
caller, so a person or an API key from any address kept full access. An invalid entry was also
passed straight to the database's CIDR column.

Now every request into the organization (browser sessions, API keys, the public API) must come
from an allowed network when the list is not empty; saving a list that would shut out the person
saving it is refused, so an owner cannot lock themselves out by mistake; and entries are
validated.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.models.organization import OrganizationMember
from app.models.identity import TenantSecurityPolicy
from app.services import api_key_service
from tests.engines.conftest import Engines

POLICY = "/identity/security-policy"
OFFICE = "203.0.113.0/24"
INSIDE = "203.0.113.9"
OUTSIDE = "198.51.100.7"


def _from(engines: Engines, address: str) -> TestClient:
    return TestClient(engines.client.app, client=(address, 41000))


def _allow_only_office(engines: Engines) -> None:
    engines.plan("enterprise")
    policy = engines.db.query(TenantSecurityPolicy).filter_by(organization_id=engines.org).one_or_none()
    if policy is None:
        policy = TenantSecurityPolicy(organization_id=engines.org)
        engines.db.add(policy)
    policy.ip_allowlist = [OFFICE]
    engines.db.commit()


def test_people_outside_the_allowed_networks_are_refused(engines: Engines) -> None:
    _allow_only_office(engines)
    headers = engines.tenant.contributor.headers

    outside = _from(engines, OUTSIDE)
    for path in (engines.url("/work-items"), engines.org_url("/members")):
        refused = outside.get(path, headers=headers)
        assert refused.status_code == 403, (path, refused.text)
        assert refused.json()["code"] == "ORGANIZATION_NETWORK_NOT_ALLOWED", refused.text

    inside = _from(engines, INSIDE)
    assert inside.get(engines.url("/work-items"), headers=headers).status_code == 200
    assert inside.get(engines.org_url("/members"), headers=headers).status_code == 200


def test_api_keys_outside_the_allowed_networks_are_refused(engines: Engines) -> None:
    _allow_only_office(engines)
    owner_membership = (
        engines.db.query(OrganizationMember)
        .filter_by(organization_id=engines.org, user_id=engines.tenant.owner.user.id)
        .one()
    )
    _, token = api_key_service.issue_api_key(
        engines.db,
        organization_id=engines.org,
        actor=owner_membership,
        name="allowlist probe",
        scopes=["work_items:read", "public_documents:read"],
    )
    engines.db.commit()
    headers = {"Authorization": f"Bearer {token}"}

    outside = _from(engines, OUTSIDE)
    tenant_route = outside.get(engines.url("/work-items"), headers=headers)
    assert tenant_route.status_code == 403, tenant_route.text
    public_route = outside.get("/api/v1/public/documents", headers=headers)
    assert public_route.status_code == 403, public_route.text

    inside = _from(engines, INSIDE)
    assert inside.get(engines.url("/work-items"), headers=headers).status_code == 200


def test_saving_a_list_that_shuts_out_the_person_saving_it_is_refused(engines: Engines) -> None:
    engines.plan("enterprise")
    owner = engines.tenant.owner.headers
    outside = _from(engines, OUTSIDE)

    refused = outside.put(engines.org_url(POLICY), json={"ip_allowlist": [OFFICE]}, headers=owner)
    assert refused.status_code == 409, refused.text
    assert OUTSIDE in refused.json()["detail"]

    inside = _from(engines, INSIDE)
    saved = inside.put(engines.org_url(POLICY), json={"ip_allowlist": [OFFICE]}, headers=owner)
    assert saved.status_code == 200, saved.text
    assert saved.json()["ip_allowlist"] == [OFFICE]

    cleared = inside.put(engines.org_url(POLICY), json={"ip_allowlist": []}, headers=owner)
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["ip_allowlist"] == []


def test_an_entry_that_is_not_a_network_is_a_validation_error(engines: Engines) -> None:
    engines.plan("enterprise")
    inside = _from(engines, INSIDE)
    response = inside.put(
        engines.org_url(POLICY),
        json={"ip_allowlist": ["not-a-network"]},
        headers=engines.tenant.owner.headers,
    )
    assert response.status_code == 422, response.text


def test_an_empty_list_allows_every_network(engines: Engines) -> None:
    engines.plan("enterprise")
    outside = _from(engines, OUTSIDE)
    assert outside.get(engines.url("/work-items"), headers=engines.tenant.contributor.headers).status_code == 200

"""F-208 — identity administration answered malformed input with a 500.

    pytest tests/api/test_identity_admin_malformed_input.py -q

Found by the live API sweep: the identity console's routes took their ids as
plain strings and handed them to `db.get`, so an id that is not a UUID reached
Postgres ("invalid input syntax for type uuid") and came back as an unhandled
500 with a traceback in the server log. The role-mapping and dry-run bodies were
read with `payload["..."]`, `int(...)` and `.items()` unchecked, with the same
result. Every such request must now be refused with a 4xx that says why.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.identity import EnterpriseIdpConfig, JitProvisioningMode, VerifiedDomain
from app.services.identity import scim_service
from tests.conftest import Fixture
from tests.security.plans import put_on_plan

API = "/api/v1"


@pytest.fixture()
def enterprise_config(db_session: Session, tenant: Fixture) -> EnterpriseIdpConfig:
    put_on_plan(db_session, tenant.organization, "enterprise")
    now = scim_service.utcnow()
    domain = VerifiedDomain(
        organization_id=tenant.organization.id,
        domain=f"{tenant.organization.slug}.test",
        status="VERIFIED",
        challenge_token="token",
        challenge_issued_at=now,
        challenge_expires_at=now + timedelta(days=30),
        first_verified_at=now,
        is_sso_binding=True,
    )
    db_session.add(domain)
    db_session.flush()
    config = EnterpriseIdpConfig(
        organization_id=tenant.organization.id,
        verified_domain_id=domain.id,
        protocol="SAML2",
        display_name="Acme SAML",
        is_active=False,
        idp_entity_id="https://idp.acme.test/saml/metadata",
        idp_sso_url="https://idp.acme.test/saml/sso",
        jit_provisioning_mode=JitProvisioningMode.INVITE_ONLY,
    )
    db_session.add(config)
    db_session.commit()
    return config


def _base(tenant: Fixture) -> str:
    return f"{API}/organizations/{tenant.organization.id}/identity"


@pytest.mark.parametrize(
    "method,leaf",
    [
        ("POST", "domains/not-a-uuid/verify"),
        ("POST", "domains/not-a-uuid/bind-sso"),
        ("POST", "idp-configs/not-a-uuid/certificates"),
        ("POST", "idp-configs/not-a-uuid/role-mappings"),
        ("POST", "idp-configs/not-a-uuid/dry-run"),
        ("POST", "idp-configs/not-a-uuid/activate"),
        ("POST", "scim-keys/not-a-uuid/rotate"),
        ("DELETE", "scim-keys/not-a-uuid"),
    ],
)
def test_a_path_id_that_is_not_a_uuid_is_not_found(
    client: TestClient, tenant: Fixture, enterprise_config, method: str, leaf: str
) -> None:
    response = client.request(
        method, f"{_base(tenant)}/{leaf}", json={}, headers=tenant.owner.headers
    )
    assert response.status_code == 404, response.text


@pytest.mark.parametrize("bad", ["not-a-uuid", 123, ["x"], {"a": 1}])
@pytest.mark.parametrize(
    "leaf,field", [("idp-configs", "verified_domain_id"), ("scim-keys", "idp_config_id")]
)
def test_a_body_id_that_is_not_a_uuid_is_not_found(
    client: TestClient, tenant: Fixture, enterprise_config, leaf: str, field: str, bad
) -> None:
    response = client.post(
        f"{_base(tenant)}/{leaf}",
        json={field: bad, "protocol": "SAML2"},
        headers=tenant.owner.headers,
    )
    assert response.status_code == 404, response.text


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"attribute_name": "groups"},
        {"attribute_name": "groups", "match_value": "x", "priority": "high"},
        {"attribute_name": "groups", "match_value": "x", "priority": 2**63},
        {"attribute_name": "groups", "match_value": "x", "match_kind": "REGEX"},
        {"attribute_name": "groups", "match_value": "x", "organization_role": "SUPERUSER"},
        {"attribute_name": "  ", "match_value": "x"},
        {"attribute_name": "groups", "match_value": ""},
        {"attribute_name": ["groups"], "match_value": "x"},
    ],
)
def test_an_invalid_role_mapping_is_refused_with_a_reason(
    client: TestClient, tenant: Fixture, enterprise_config, body
) -> None:
    response = client.post(
        f"{_base(tenant)}/idp-configs/{enterprise_config.id}/role-mappings",
        json=body,
        headers=tenant.owner.headers,
    )
    assert response.status_code == 422, response.text
    assert response.json().get("detail")


def test_a_valid_role_mapping_is_still_saved(
    client: TestClient, tenant: Fixture, enterprise_config
) -> None:
    response = client.post(
        f"{_base(tenant)}/idp-configs/{enterprise_config.id}/role-mappings",
        json={
            "attribute_name": "groups",
            "match_kind": "contains",
            "match_value": "finance",
            "organization_role": "admin",
            "priority": 10,
        },
        headers=tenant.owner.headers,
    )
    assert response.status_code == 201, response.text
    assert response.json()["priority"] == 10


@pytest.mark.parametrize("attributes", [["groups"], "groups", 5])
def test_a_dry_run_with_attributes_that_are_not_an_object_is_refused(
    client: TestClient, tenant: Fixture, enterprise_config, attributes
) -> None:
    response = client.post(
        f"{_base(tenant)}/idp-configs/{enterprise_config.id}/dry-run",
        json={"attributes": attributes},
        headers=tenant.owner.headers,
    )
    assert response.status_code == 422, response.text


def test_an_unknown_default_role_on_a_new_connection_is_refused(
    client: TestClient, tenant: Fixture, enterprise_config
) -> None:
    response = client.post(
        f"{_base(tenant)}/idp-configs",
        json={
            "verified_domain_id": str(enterprise_config.verified_domain_id),
            "protocol": "SAML2",
            "jit_default_org_role": "SUPERUSER",
        },
        headers=tenant.owner.headers,
    )
    assert response.status_code == 422, response.text

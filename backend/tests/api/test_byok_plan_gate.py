"""N-021 / F-095 — bring your own AI key is a Business and Enterprise feature.

Before the owner's decision every plan, Free included, could store provider
keys and routing rules. Now the four writes (store or rotate a key, validate
it, change its fallback policy, save a routing rule) need `capability.byok`.
Reading what is configured and retiring a key stay open on every plan, so a
tenant that downgrades can still see and remove its credentials (N-003).
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core import entitlements
from app.core.byok_providers import PROVIDER_GROQ
from app.services.byok import credential_service
from tests.conftest import Fixture
from tests.security.plans import put_on_plan

GROQ_KEY = "gsk_" + "d" * 48


def base(organization_id) -> str:
    return f"/api/v1/organizations/{organization_id}/byok"


def _writes(organization_id) -> list[tuple[str, str, dict | None]]:
    root = base(organization_id)
    return [
        ("PUT", f"{root}/credentials", {"provider": PROVIDER_GROQ, "api_key": GROQ_KEY}),
        ("POST", f"{root}/credentials/{PROVIDER_GROQ}/validate", None),
        ("PUT", f"{root}/credentials/{PROVIDER_GROQ}/fallback", {"allow_platform_fallback": True}),
        (
            "PUT",
            f"{root}/routes",
            {"task_type": "EXTRACTION", "provider": PROVIDER_GROQ, "model_name": "llama-3.3-70b-versatile",
             "use_tenant_key": False},
        ),
    ]


def _store_key(db: Session, tenant: Fixture) -> None:
    credential_service.upsert_credential(
        db, organization_id=tenant.organization.id, provider=PROVIDER_GROQ, plaintext_key=GROQ_KEY
    )
    db.commit()


@pytest.mark.parametrize("plan", ["free", "developer"])
def test_every_byok_write_is_refused_below_business(
    client: TestClient, db_session: Session, tenant: Fixture, plan: str
) -> None:
    put_on_plan(db_session, tenant.organization, plan)
    _store_key(db_session, tenant)  # so validate/fallback reach the gate, not a 404

    for method, url, body in _writes(tenant.organization.id):
        response = client.request(method, url, json=body, headers=tenant.owner.headers)
        assert response.status_code == 402, (method, url, response.text)
        details = response.json().get("details") or {}
        assert details.get("code") == "CAPABILITY_REQUIRED", response.text
        assert details.get("capability_key") == entitlements.BYOK_CAPABILITY


@pytest.mark.parametrize("plan", ["business", "enterprise"])
def test_business_and_enterprise_may_store_a_key_and_a_route(
    client: TestClient, db_session: Session, tenant: Fixture, plan: str
) -> None:
    put_on_plan(db_session, tenant.organization, plan)
    root = base(tenant.organization.id)

    stored = client.put(
        f"{root}/credentials",
        json={"provider": PROVIDER_GROQ, "api_key": GROQ_KEY},
        headers=tenant.owner.headers,
    )
    assert stored.status_code == 200, stored.text

    method, url, body = _writes(tenant.organization.id)[3]
    routed = client.request(method, url, json=body, headers=tenant.owner.headers)
    assert routed.status_code == 200, routed.text


def test_a_downgraded_tenant_can_still_read_and_retire_its_key(
    client: TestClient, db_session: Session, tenant: Fixture
) -> None:
    put_on_plan(db_session, tenant.organization, "free")
    _store_key(db_session, tenant)
    root = base(tenant.organization.id)

    for path in ("", "/providers", "/credentials", "/routes", "/savings"):
        response = client.get(f"{root}{path}", headers=tenant.owner.headers)
        assert response.status_code == 200, (path, response.text)

    listed = client.get(f"{root}/credentials", headers=tenant.owner.headers).json()
    assert any(row["provider"] == PROVIDER_GROQ for row in listed)

    retired = client.delete(f"{root}/credentials/{PROVIDER_GROQ}", headers=tenant.owner.headers)
    assert retired.status_code in (200, 204), retired.text


def test_the_tiers_that_include_byok_are_business_and_enterprise() -> None:
    from tests.security.plans import _seed

    matrix = _seed().capability_matrix()
    holders = sorted(plan for plan, keys in matrix.items() if entitlements.BYOK_CAPABILITY in keys)
    assert holders == ["business", "enterprise"]

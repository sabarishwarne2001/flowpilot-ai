"""Helm through API keys (Enterprise, `capability.helm_api`), on real Postgres.

* Default deny: a key that holds every scope except Helm's is refused (403) on every Helm route, read
  or write, and the refusal names the missing scope (or says no scope covers the route).
* A key reaches Helm only on a plan with `capability.helm_api`: elsewhere it is refused (402) in words
  that say what to do. A person signed in is not affected by that rule.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.billing_errors import CapabilityRequiredError
from app.core.principal import Principal, reset_current_principal, set_current_principal
from app.core.scopes import ApiKeyScope
from app.models.organization import OrganizationMember
from app.services import api_key_service
from app.services.helm import service
from tests.conftest import Fixture
from tests.security.plans import put_on_plan

API = "/api/v1"
HELM = "/organizations/{organization_id}/helm"


def _key(db: Session, tenant: Fixture, scopes: list[str], plan: str = "enterprise") -> dict[str, str]:
    put_on_plan(db, tenant.organization, plan)
    membership = db.query(OrganizationMember).filter_by(organization_id=tenant.organization.id,
                                                        user_id=tenant.owner.user.id).one()
    _key_row, token = api_key_service.issue_api_key(db, organization_id=tenant.organization.id, actor=membership,
                                                    name=f"helm probe {uuid.uuid4().hex[:6]}", scopes=scopes)
    db.commit()
    return {"Authorization": f"Bearer {token}"}


def _helm_routes(client: TestClient) -> list[tuple[str, str]]:
    found = []
    for route in client.app.routes:
        path = getattr(route, "path", "")
        if path.startswith(f"{API}{HELM}"):
            found.extend((method, path) for method in sorted(getattr(route, "methods", None) or ()))
    return found


def test_a_key_without_helms_scopes_is_refused_on_every_helm_route(
    client: TestClient, db_session: Session, tenant: Fixture
) -> None:
    others = [s.value for s in ApiKeyScope if not s.value.startswith("helm:")]
    headers = _key(db_session, tenant, others)
    routes = _helm_routes(client)
    assert len(routes) >= 40, routes
    for method, path in routes:
        url = path.replace("{organization_id}", str(tenant.organization.id))
        for name in ("book_id", "workspace_id", "mandate_id", "action_id", "certificate_id", "counterparty_id",
                     "account_id", "line_id", "backtest_id", "work_item_id"):
            url = url.replace("{" + name + "}", str(uuid.uuid4()))
        response = client.request(method, url, headers=headers, json={})
        assert response.status_code == 403, (method, path, response.status_code, response.text[:200])
        assert "scope" in response.text, (method, path, response.text[:200])


def _as_api_key(user_id: uuid.UUID):
    return set_current_principal(Principal.for_api_key(api_key_id=uuid.uuid4(), issuer_user_id=user_id))


def test_a_key_on_a_plan_without_helm_api_is_refused_in_words(db_session: Session, tenant: Fixture) -> None:
    put_on_plan(db_session, tenant.organization, "business")
    db_session.commit()
    token = _as_api_key(tenant.owner.user.id)
    try:
        with pytest.raises(CapabilityRequiredError) as refused:
            service.viewer(db_session, organization_id=tenant.organization.id, user_id=tenant.owner.user.id,
                           role="OWNER")
    finally:
        reset_current_principal(token)
    words = str(refused.value)
    assert "Enterprise" in words and "API keys" in words, words
    assert refused.value.details["capability"] == "capability.helm_api"
    assert refused.value.details["remedy"] == "UPGRADE_PLAN"


def test_a_key_on_enterprise_and_a_person_on_business_both_reach_helm(db_session: Session, tenant: Fixture) -> None:
    put_on_plan(db_session, tenant.organization, "enterprise")
    db_session.commit()
    token = _as_api_key(tenant.owner.user.id)
    try:
        who = service.viewer(db_session, organization_id=tenant.organization.id, user_id=tenant.owner.user.id,
                             role="OWNER")
    finally:
        reset_current_principal(token)
    assert who.helm and who.decides

    put_on_plan(db_session, tenant.organization, "business")
    db_session.commit()
    who = service.viewer(db_session, organization_id=tenant.organization.id, user_id=tenant.owner.user.id,
                         role="OWNER")
    assert who.helm, "a person signed in on Business keeps Helm; only API keys need Enterprise"

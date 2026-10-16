"""Helm through API keys (Enterprise, `capability.helm_api`), on real Postgres.

* Default deny: a key that holds every scope except Helm's is refused (403) on every Helm route, read
  or write, and the refusal names the missing scope (or says no scope covers the route).
* A key reaches Helm only on a plan with `capability.helm_api`: elsewhere it is refused (402) in words
  that say what to do. A person signed in is not affected by that rule.
* With helm:read a key reads Helm; with helm:write it may feed Helm facts and re-plan. Approving,
  rejecting or undoing an action, and Helm's rules and set-up (books, settings, counterparties,
  mandates, the sample company), stay with people: no key may do them, and it is told so in words.
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


# ---------------------------------------------------------------------------
# With the vocabulary (migration h1a4_helm_api_vocabulary): helm:read and helm:write
# ---------------------------------------------------------------------------


def test_every_helm_route_names_its_scope_or_is_kept_for_people(client: TestClient) -> None:
    """GET reads with helm:read; the POST, PATCH and DELETE a key may make need helm:write; every other
    Helm route is kept for people, with its reason. A Helm route added without an entry, an entry for a
    route that is gone, or a route in both lists, fails here."""
    from app.core.scopes import PEOPLE_ONLY_ROUTES, ROUTE_SCOPE_MAP

    mapped = {(method, f"{API}{path}"): scope for (method, path), scope in ROUTE_SCOPE_MAP.items()
              if path.startswith(HELM)}
    people = {(method, f"{API}{path}") for (method, path) in PEOPLE_ONLY_ROUTES if path.startswith(HELM)}
    assert not set(mapped) & people, set(mapped) & people
    assert set(mapped) | people == set(_helm_routes(client))
    for (method, path), scope in mapped.items():
        expected = ApiKeyScope.HELM_READ if method == "GET" else ApiKeyScope.HELM_WRITE
        assert scope is expected, (method, path, scope)
    kept = {path.split("/helm", 1)[1] for _method, path in people}
    for decision in ("/actions/{action_id}/approve", "/actions/{action_id}/reject", "/actions/{action_id}/undo",
                     "/books/{book_id}/plan/approve-all", "/books/{book_id}/mandates", "/books/{book_id}/settings"):
        assert decision in kept, decision


def _book(client: TestClient, tenant: Fixture) -> str:
    response = client.post(f"{API}/organizations/{tenant.organization.id}/helm/books",
                           json={"workspace_id": str(tenant.workspace.id)}, headers=tenant.owner.headers)
    assert response.status_code == 201, response.text
    return response.json()["id"]


def test_a_read_key_reads_helm_and_cannot_change_it(client: TestClient, db_session: Session,
                                                     tenant: Fixture) -> None:
    put_on_plan(db_session, tenant.organization, "enterprise")
    db_session.commit()
    book = _book(client, tenant)
    url = f"{API}/organizations/{tenant.organization.id}/helm"
    reader = _key(db_session, tenant, [ApiKeyScope.HELM_READ.value])
    for path in ("", f"/books/{book}", f"/books/{book}/cockpit", f"/books/{book}/mandates", "/library"):
        response = client.get(f"{url}{path}", headers=reader)
        assert response.status_code == 200, (path, response.text[:300])
    for method, path, body in (("POST", f"/books/{book}/plan", None),
                               ("POST", f"/books/{book}/cash-positions",
                                {"currency": "INR", "amount": "250000", "as_of": "2026-10-01"}),
                               ("POST", "/backtests", {"book_id": book, "months": 3})):
        response = client.request(method, f"{url}{path}", headers=reader, json=body)
        assert response.status_code == 403, (method, path, response.text[:300])
        assert "helm:write" in response.text, response.text

    writer = _key(db_session, tenant, [ApiKeyScope.HELM_READ.value, ApiKeyScope.HELM_WRITE.value])
    planned = client.post(f"{url}/books/{book}/plan", headers=writer)
    assert planned.status_code == 200, planned.text
    assert planned.json()["plan"]["version"] >= 1
    cash = client.post(f"{url}/books/{book}/cash-positions", headers=writer,
                       json={"currency": "INR", "amount": "250000", "as_of": "2026-10-01"})
    assert cash.status_code == 201, cash.text


def test_a_write_key_is_told_in_words_that_decisions_and_rules_stay_with_people(
        client: TestClient, db_session: Session, tenant: Fixture) -> None:
    put_on_plan(db_session, tenant.organization, "enterprise")
    db_session.commit()
    book = _book(client, tenant)
    url = f"{API}/organizations/{tenant.organization.id}/helm"
    writer = _key(db_session, tenant, [ApiKeyScope.HELM_READ.value, ApiKeyScope.HELM_WRITE.value])
    action = str(uuid.uuid4())
    for method, path, body, words in (
            ("POST", f"/actions/{action}/approve", None, "person's decision"),
            ("POST", f"/actions/{action}/reject", {"reason": "not today"}, "person's decision"),
            ("POST", f"/actions/{action}/undo", None, "person's decision"),
            ("POST", f"/books/{book}/plan/approve-all", None, "person's decision"),
            ("POST", f"/books/{book}/mandates", {"template": "pay_on_time"}, "signed in"),
            ("PATCH", f"/books/{book}/settings", {"default_autonomy": "act"}, "signed in"),
            ("DELETE", "/sample", None, "signed in")):
        response = client.request(method, f"{url}{path}", headers=writer, json=body)
        assert response.status_code == 403, (method, path, response.text[:300])
        assert words in response.json()["message"], (method, path, response.text[:300])
    # The book's settings are untouched, and a person signed in still makes the change.
    settings = client.get(f"{url}/books/{book}/settings", headers=writer).json()
    assert settings["default_autonomy"] != "act", settings
    assert client.patch(f"{url}/books/{book}/settings", json={"horizon_days": 30},
                        headers=tenant.owner.headers).status_code == 200


def test_a_key_on_business_is_refused_in_words_and_a_person_is_not(client: TestClient, db_session: Session,
                                                                    tenant: Fixture) -> None:
    url = f"{API}/organizations/{tenant.organization.id}/helm"
    headers = _key(db_session, tenant, [ApiKeyScope.HELM_READ.value, ApiKeyScope.HELM_WRITE.value],
                   plan="business")
    refused = client.get(url, headers=headers)
    assert refused.status_code == 402, refused.text
    body = refused.json()
    assert body["code"] == "CAPABILITY_REQUIRED", body
    assert "Enterprise" in body["message"] and "API keys" in body["message"], body
    assert client.get(url, headers=tenant.owner.headers).status_code == 200


def test_a_billing_members_key_never_carries_helm_write() -> None:
    from app.core.scopes import effective_scopes
    from app.models.api_key import ApiKey
    from app.models.organization import MembershipStatus, OrganizationRole

    key = ApiKey(scopes=[ApiKeyScope.HELM_READ.value, ApiKeyScope.HELM_WRITE.value])
    billing = OrganizationMember(role=OrganizationRole.BILLING, status=MembershipStatus.ACTIVE)
    member = OrganizationMember(role=OrganizationRole.MEMBER, status=MembershipStatus.ACTIVE)
    assert effective_scopes(key, billing) == frozenset({ApiKeyScope.HELM_READ})
    assert effective_scopes(key, member) == frozenset()


def _refusals(db: Session, tenant: Fixture) -> list:
    from sqlalchemy import select

    from app.models.audit_log import AuditLog, AuditOutcome

    db.expire_all()
    return list(db.execute(select(AuditLog).where(AuditLog.organization_id == tenant.organization.id,
                                                  AuditLog.outcome == AuditOutcome.DENIED)
                           .order_by(AuditLog.created_at)).scalars())


def test_every_refusal_of_a_key_is_audited_with_the_key_and_the_reason(client: TestClient, db_session: Session,
                                                                         tenant: Fixture) -> None:
    """Through the denial aggregation (one row each, then summary rows past 50 an hour), so a script
    hammering Helm with a refused key cannot fill the audit log."""
    url = f"{API}/organizations/{tenant.organization.id}/helm"
    reader = _key(db_session, tenant, [ApiKeyScope.HELM_READ.value])
    writer = _key(db_session, tenant, [ApiKeyScope.HELM_READ.value, ApiKeyScope.HELM_WRITE.value])
    assert client.post(f"{url}/backtests", json={}, headers=reader).status_code == 403
    assert client.post(f"{url}/actions/{uuid.uuid4()}/approve", headers=writer).status_code == 403
    put_on_plan(db_session, tenant.organization, "business")
    db_session.commit()
    assert client.get(url, headers=writer).status_code == 402

    rows = _refusals(db_session, tenant)
    reasons = [row.details.get("reason") for row in rows]
    assert reasons == ["api_key_scope", "people_only", "capability_required"], [row.details for row in rows]
    scope, people, plan = rows
    assert scope.api_key_id is not None and scope.details["required_scope"] == "helm:write", scope.details
    assert scope.details["route"] == "POST /organizations/{organization_id}/helm/backtests", scope.details
    assert people.details["route"] == "POST /organizations/{organization_id}/helm/actions/{action_id}/approve"
    assert plan.details["capability_key"] == "capability.helm_api" and plan.api_key_id == people.api_key_id
    # The platform's convention for a key's acts: the key is the actor, its owner is in the details.
    assert scope.details["key_owner_user_id"] == str(tenant.owner.user.id), scope.details

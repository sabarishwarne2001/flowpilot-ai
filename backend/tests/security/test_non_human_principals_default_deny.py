"""Non-human principals reach only their own routes (campaign session 3; the F-271 follow-up).

F-271 found that an API key acted with its issuer's full rights wherever a route forgot to check
a scope. The same question for every other credential a machine holds:

- a SCIM token opens the directory (`/scim/v2`) of its own organization and nothing else;
- an API key, whatever scopes it holds, opens no SCIM directory and no partner portal route;
- the public gateway refuses a key that lacks the route's scope, on every gateway route, so a
  gateway route added without its scope check fails here.

The partner portal has no token of its own: partner members are people signed in to Servabo.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.api.deps import require_api_key
from app.core.scopes import PUBLIC_API_SCOPES, ROUTE_SCOPE_MAP, ApiKeyScope
from app.models.identity import DomainStatus, EnterpriseIdpConfig, IdpProtocol, VerifiedDomain
from app.models.organization import OrganizationMember
from app.services import api_key_service
from app.services.identity import scim_service
from tests.conftest import Fixture
from tests.security.plans import put_on_plan

API = "/api/v1"


def _key(db: Session, tenant: Fixture, scopes: list[str], *, public_api: bool = False) -> dict[str, str]:
    put_on_plan(db, tenant.organization, "enterprise")
    membership = (
        db.query(OrganizationMember)
        .filter_by(organization_id=tenant.organization.id, user_id=tenant.owner.user.id)
        .one()
    )
    key, token = api_key_service.issue_api_key(
        db,
        organization_id=tenant.organization.id,
        actor=membership,
        name=f"principal probe {uuid.uuid4().hex[:6]}",
        scopes=scopes,
    )
    key.is_public_api_enabled = public_api
    db.commit()
    return {"Authorization": f"Bearer {token}"}


def _scim_token(db: Session, tenant: Fixture) -> dict[str, str]:
    now = datetime.now(timezone.utc)
    domain = VerifiedDomain(
        organization_id=tenant.organization.id,
        domain=f"scim-{uuid.uuid4().hex[:6]}.example.com",
        status=DomainStatus.VERIFIED,
        challenge_token=f"challenge-{uuid.uuid4().hex[:8]}",
        challenge_expires_at=now + timedelta(days=1),
        first_verified_at=now,
        last_seen_at=now,
        is_sso_binding=True,
    )
    db.add(domain)
    db.flush()
    config = EnterpriseIdpConfig(
        organization_id=tenant.organization.id,
        verified_domain_id=domain.id,
        protocol=IdpProtocol.SAML2,
        display_name="Directory",
        is_active=True,
        idp_entity_id=f"http://idp.example/{domain.domain}",
        idp_sso_url=f"https://idp.example/{domain.domain}/sso",
        jit_seat_cap=10,
    )
    db.add(config)
    db.flush()
    _, token = scim_service.issue_key(
        db, organization_id=tenant.organization.id, idp_config_id=config.id, display_name="probe"
    )
    db.commit()
    return {"Authorization": f"Bearer {token}"}


def _tenant_routes(tenant: Fixture) -> list[str]:
    org, ws = tenant.organization.id, tenant.workspace.id
    return [
        f"{API}/organizations/slug-available?slug=probe",
        f"{API}/organizations/{org}",
        f"{API}/organizations/{org}/members",
        f"{API}/organizations/{org}/identity/security-policy",
        f"{API}/workspaces/{ws}/work-items",
        f"{API}/me/profile",
        f"{API}/auth/sessions",
        f"{API}/partners",
        f"{API}/public?workspace_id={ws}",
        f"{API}/public/documents?workspace_id={ws}",
    ]


def test_a_scim_token_opens_its_directory_and_nothing_else(
    client: TestClient, db_session: Session, tenant: Fixture
) -> None:
    headers = _scim_token(db_session, tenant)
    assert client.get("/scim/v2/Users", headers=headers).status_code == 200

    for path in _tenant_routes(tenant):
        response = client.get(path, headers=headers)
        assert response.status_code == 401, (path, response.status_code, response.text[:200])


def test_an_api_key_opens_no_directory_and_no_partner_route(
    client: TestClient, db_session: Session, tenant: Fixture
) -> None:
    headers = _key(db_session, tenant, [scope.value for scope in ApiKeyScope])
    for path in ("/scim/v2/Users", "/scim/v2/Groups", "/scim/v2/ServiceProviderConfig"):
        response = client.get(path, headers=headers)
        assert response.status_code in (401, 404), (path, response.status_code)
        assert "Resources" not in response.text

    for path in (f"{API}/partners", f"{API}/partners/{uuid.uuid4()}"):
        response = client.get(path, headers=headers)
        assert response.status_code == 403, (path, response.status_code, response.text[:200])


def _depends_on(dependant, target) -> bool:
    return any(dep.call is target or _depends_on(dep, target) for dep in dependant.dependencies)


def _gateway_routes(client: TestClient) -> list[APIRoute]:
    """Every route authenticated by the public API key dependency, wherever it is mounted."""
    routes = [
        route
        for route in client.app.routes
        if isinstance(route, APIRoute) and _depends_on(route.dependant, require_api_key)
    ]
    assert len(routes) >= 6, [route.path for route in routes]
    return routes


def test_every_gateway_route_refuses_a_key_without_its_scope(
    client: TestClient, db_session: Session, tenant: Fixture
) -> None:
    """Each gateway route names its scope in ROUTE_SCOPE_MAP, and a key holding every OTHER
    public scope is refused there (403): a route added without its scope check fails here."""
    ws = str(tenant.workspace.id)
    bodies = {
        "/public/query": {"workspace_id": ws, "query": "invoice"},
        "/public/workflows/{rule_id}/trigger": {"workspace_id": ws, "work_item_id": str(uuid.uuid4())},
    }
    checked = 0
    for route in _gateway_routes(client):
        local = route.path[len(API):]
        for method in sorted(route.methods):
            if local == "/public":
                continue  # the version route: any enabled key may read which scopes it holds
            required = ROUTE_SCOPE_MAP.get((method, local))
            assert required is not None, f"{method} {local} has no scope in ROUTE_SCOPE_MAP"
            others = [scope.value for scope in PUBLIC_API_SCOPES if scope is not required]
            headers = _key(db_session, tenant, others, public_api=True)
            path = route.path.replace("{work_item_id}", str(uuid.uuid4())).replace(
                "{rule_id}", str(uuid.uuid4())
            )
            response = client.request(
                method, f"{path}?workspace_id={ws}", headers=headers, json=bodies.get(local)
            )
            assert response.status_code == 403, (method, local, response.status_code, response.text[:200])
            assert required.value in response.text
            checked += 1
    assert checked >= 5


def test_the_version_route_names_only_the_scopes_the_key_holds(
    client: TestClient, db_session: Session, tenant: Fixture
) -> None:
    headers = _key(db_session, tenant, [ApiKeyScope.PUBLIC_DOCUMENTS_READ.value], public_api=True)
    response = client.get(f"{API}/public", headers=headers)
    assert response.status_code == 200, response.text
    assert response.json()["supported_scopes"] == [ApiKeyScope.PUBLIC_DOCUMENTS_READ.value]

"""ARCH-21 — public gateway authentication, scopes, headers and versioning."""

from __future__ import annotations

import uuid

import pytest
from fastapi import status
from sqlalchemy import select

from app.core.api_key_secret import (
    generate_secret,
    hash_secret,
    mint_api_key_token,
)
from app.core.api_tiers import ApiRateTier, ef_search_for, profile_for
from app.core.config import settings
from app.core.public_route_registry import PUBLIC_ROUTES
from app.core.scopes import PUBLIC_API_SCOPES, ROUTE_SCOPE_MAP, ApiKeyScope
from app.main import CORS_EXPOSED_HEADERS
from app.middleware.public_rate_limit import RATE_LIMIT_HEADERS
from app.models.api_key import ApiKey
from app.models.public_api import ApiKeyUsageDaily
from app.models.usage_event import UsageEvent

GATEWAY = "/api/v1/public"

ALL_PUBLIC_SCOPES = [scope.value for scope in PUBLIC_API_SCOPES]


# ===========================================================================
# Helpers
# ===========================================================================


def _mint(
    db,
    organization_id,
    issuer_user_id,
    *,
    scopes: list[str] | None = None,
    tier: ApiRateTier = ApiRateTier.PRO,
    enabled: bool = True,
) -> tuple[ApiKey, str]:
    secret = generate_secret()
    key = ApiKey(
        organization_id=organization_id,
        user_id=issuer_user_id,
        name=f"gw-{uuid.uuid4().hex[:8]}",
        secret_hash=hash_secret(secret),
        scopes=scopes if scopes is not None else list(ALL_PUBLIC_SCOPES),
        tier_key=tier.value,
        rate_limit_per_minute=profile_for(tier).rate_limit_per_minute,
        monthly_request_quota=profile_for(tier).monthly_request_quota,
        is_public_api_enabled=enabled,
    )
    db.add(key)
    db.commit()
    db.refresh(key)
    return key, mint_api_key_token(key.id, secret)


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# ===========================================================================
# Startup wiring
# ===========================================================================


def test_the_app_boots_with_the_gateway_mounted(client) -> None:
    response = client.get(f"{settings.API_V1_STR}/health")
    assert response.status_code == status.HTTP_200_OK


def test_no_gateway_route_is_registered_as_public() -> None:
    offenders = [r.path for r in PUBLIC_ROUTES if r.path.startswith(GATEWAY)]
    assert offenders == []


def test_rate_limit_headers_are_cors_exposed() -> None:
    for header in RATE_LIMIT_HEADERS:
        assert header in CORS_EXPOSED_HEADERS, f"{header} not exposed"


def test_every_gateway_route_has_a_scope_map_entry() -> None:
    mapped = {path for (_method, path) in ROUTE_SCOPE_MAP}
    for path in (
        "/public/documents",
        "/public/documents/{work_item_id}",
        "/public/query",
        "/public/workflows",
        "/public/workflows/{rule_id}/trigger",
    ):
        assert path in mapped, f"{path} is unmapped"


# ===========================================================================
# Authentication
# ===========================================================================


def test_no_credential_is_refused(client) -> None:
    response = client.get(
        f"{GATEWAY}/documents", params={"workspace_id": str(uuid.uuid4())}
    )
    assert response.status_code == status.HTTP_401_UNAUTHORIZED


def test_a_session_jwt_is_refused_on_the_gateway(client, tenant) -> None:
    response = client.get(
        f"{GATEWAY}/documents",
        params={"workspace_id": str(tenant.workspace.id)},
        headers=tenant.owner.headers,
    )
    assert response.status_code == status.HTTP_401_UNAUTHORIZED


def test_a_garbage_token_is_refused(client) -> None:
    response = client.get(
        f"{GATEWAY}/documents",
        params={"workspace_id": str(uuid.uuid4())},
        headers=_auth("fp_test_NOTAREALKEY_nope"),
    )
    assert response.status_code == status.HTTP_401_UNAUTHORIZED


def test_a_key_not_enabled_for_the_gateway_gets_403_not_401(
    client, db_session, tenant
) -> None:
    _key, token = _mint(
        db_session,
        tenant.organization.id,
        tenant.org_admin.user.id,
        enabled=False,
        scopes=[ApiKeyScope.ORGANIZATIONS_READ.value],
    )
    response = client.get(
        f"{GATEWAY}/documents",
        params={"workspace_id": str(tenant.workspace.id)},
        headers=_auth(token),
    )
    assert response.status_code == status.HTTP_403_FORBIDDEN
    assert "developer portal" in response.json()["detail"].lower()


def test_an_enabled_key_reaches_the_gateway(client, db_session, tenant) -> None:
    _key, token = _mint(
        db_session, tenant.organization.id, tenant.org_admin.user.id
    )
    response = client.get(
        f"{GATEWAY}/documents",
        params={"workspace_id": str(tenant.workspace.id)},
        headers=_auth(token),
    )
    assert response.status_code == status.HTTP_200_OK
    body = response.json()
    assert body["items"] == []
    assert body["total"] == 0


# ===========================================================================
# Scope enforcement
# ===========================================================================


def test_a_key_without_the_query_scope_cannot_query(
    client, db_session, tenant
) -> None:
    _key, token = _mint(
        db_session,
        tenant.organization.id,
        tenant.org_admin.user.id,
        scopes=[ApiKeyScope.PUBLIC_DOCUMENTS_READ.value],
    )
    response = client.post(
        f"{GATEWAY}/query",
        json={
            "workspace_id": str(tenant.workspace.id),
            "query": "payment terms",
            "top_k": 3,
        },
        headers=_auth(token),
    )
    assert response.status_code == status.HTTP_403_FORBIDDEN
    assert "public_query:write" in response.json()["detail"]


def test_a_key_without_the_workflow_write_scope_cannot_trigger(
    client, db_session, tenant
) -> None:
    _key, token = _mint(
        db_session,
        tenant.organization.id,
        tenant.org_admin.user.id,
        scopes=[ApiKeyScope.PUBLIC_WORKFLOWS_READ.value],
    )
    response = client.post(
        f"{GATEWAY}/workflows/{uuid.uuid4()}/trigger",
        json={
            "workspace_id": str(tenant.workspace.id),
            "work_item_id": str(uuid.uuid4()),
        },
        headers=_auth(token),
    )
    assert response.status_code == status.HTTP_403_FORBIDDEN


def test_an_old_console_key_cannot_reach_the_gateway(
    client, db_session, tenant
) -> None:
    _key, token = _mint(
        db_session,
        tenant.organization.id,
        tenant.org_admin.user.id,
        scopes=[
            ApiKeyScope.WORK_ITEMS_READ.value,
            ApiKeyScope.PUBLIC_WORKFLOWS_READ.value,
        ],
    )
    response = client.get(
        f"{GATEWAY}/documents",
        params={"workspace_id": str(tenant.workspace.id)},
        headers=_auth(token),
    )
    assert response.status_code == status.HTTP_403_FORBIDDEN


# ===========================================================================
# Tenancy
# ===========================================================================


def test_a_key_cannot_read_another_tenants_workspace(
    client, db_session, tenant
) -> None:
    _key, token = _mint(
        db_session, tenant.organization.id, tenant.org_admin.user.id
    )
    response = client.get(
        f"{GATEWAY}/documents",
        params={"workspace_id": str(tenant.foreign_workspace.id)},
        headers=_auth(token),
    )
    assert response.status_code == status.HTTP_404_NOT_FOUND


def test_absent_and_foreign_workspaces_are_indistinguishable(
    client, db_session, tenant
) -> None:
    _key, token = _mint(
        db_session, tenant.organization.id, tenant.org_admin.user.id
    )
    absent = client.get(
        f"{GATEWAY}/documents",
        params={"workspace_id": str(uuid.uuid4())},
        headers=_auth(token),
    )
    foreign = client.get(
        f"{GATEWAY}/documents",
        params={"workspace_id": str(tenant.foreign_workspace.id)},
        headers=_auth(token),
    )
    assert absent.status_code == foreign.status_code == status.HTTP_404_NOT_FOUND
    assert absent.json() == foreign.json()


# ===========================================================================
# Rate limit headers
# ===========================================================================


def test_both_header_spellings_are_emitted(client, db_session, tenant) -> None:
    _key, token = _mint(
        db_session,
        tenant.organization.id,
        tenant.org_admin.user.id,
        tier=ApiRateTier.BUILDER,
    )
    response = client.get(
        f"{GATEWAY}/documents",
        params={"workspace_id": str(tenant.workspace.id)},
        headers=_auth(token),
    )
    assert response.status_code == status.HTTP_200_OK

    expected_limit = str(profile_for(ApiRateTier.BUILDER).rate_limit_per_minute)
    assert response.headers["X-RateLimit-Limit"] == expected_limit
    assert response.headers["RateLimit-Limit"] == expected_limit
    assert response.headers["X-RateLimit-Tier"] == ApiRateTier.BUILDER.value
    assert "X-RateLimit-Remaining" in response.headers
    assert "X-RateLimit-Reset" in response.headers


def test_the_body_mirrors_the_headers(client, db_session, tenant) -> None:
    _key, token = _mint(
        db_session,
        tenant.organization.id,
        tenant.org_admin.user.id,
        tier=ApiRateTier.PRO,
    )
    response = client.get(
        f"{GATEWAY}/documents",
        params={"workspace_id": str(tenant.workspace.id)},
        headers=_auth(token),
    )
    snapshot = response.json()["rate_limit"]
    assert snapshot["tier"] == ApiRateTier.PRO.value
    assert str(snapshot["limit"]) == response.headers["X-RateLimit-Limit"]
    assert str(snapshot["remaining"]) == response.headers["X-RateLimit-Remaining"]


def test_the_429_path_carries_its_own_headers() -> None:
    import inspect
    from app.api import deps

    source = inspect.getsource(deps.require_api_key)
    assert "HTTP_429_TOO_MANY_REQUESTS" in source
    for header in ("X-RateLimit-Limit", "X-RateLimit-Remaining", "X-RateLimit-Reset"):
        assert header in source, f"the 429 raised in require_api_key omits {header}"
    assert "Retry-After" in source


def test_an_unauthenticated_request_gets_no_invented_limit(client) -> None:
    response = client.get(
        f"{GATEWAY}/documents", params={"workspace_id": str(uuid.uuid4())}
    )
    assert response.status_code == status.HTTP_401_UNAUTHORIZED
    assert "X-RateLimit-Limit" not in response.headers


# ===========================================================================
# Version and deprecation contract
# ===========================================================================


def test_the_version_endpoint_reports_only_this_keys_scopes(
    client, db_session, tenant
) -> None:
    _key, token = _mint(
        db_session,
        tenant.organization.id,
        tenant.org_admin.user.id,
        scopes=[
            ApiKeyScope.PUBLIC_DOCUMENTS_READ.value,
            ApiKeyScope.PUBLIC_QUERY_WRITE.value,
        ],
    )
    response = client.get(GATEWAY, headers=_auth(token))
    assert response.status_code == status.HTTP_200_OK
    body = response.json()
    assert body["status"] == "STABLE"
    assert set(body["supported_scopes"]) == {
        "public_documents:read",
        "public_query:write",
    }
    assert response.headers["X-FlowPilot-API-Version"] == body["version"]


def test_the_version_endpoint_is_authenticated(client) -> None:
    assert client.get(GATEWAY).status_code == status.HTTP_401_UNAUTHORIZED


def test_deprecation_headers_are_absent_while_v1_is_current(
    client, db_session, tenant
) -> None:
    _key, token = _mint(
        db_session, tenant.organization.id, tenant.org_admin.user.id
    )
    response = client.get(GATEWAY, headers=_auth(token))
    assert "Sunset" not in response.headers
    assert "Deprecation" not in response.headers
    assert 'rel="describedby"' in response.headers["Link"]


# ===========================================================================
# Metering through the HTTP path
# ===========================================================================


def test_a_served_request_is_metered_into_both_stores(
    client, db_session, tenant
) -> None:
    key, token = _mint(
        db_session, tenant.organization.id, tenant.org_admin.user.id
    )
    response = client.get(
        f"{GATEWAY}/documents",
        params={"workspace_id": str(tenant.workspace.id)},
        headers=_auth(token),
    )
    assert response.status_code == status.HTTP_200_OK

    events = (
        db_session.execute(
            select(UsageEvent).where(
                UsageEvent.event_type == "api.request",
                UsageEvent.api_key_id == key.id,
            )
        )
        .scalars()
        .all()
    )
    assert len(events) == 1
    assert events[0].actor_id is None

    rollup = db_session.execute(
        select(ApiKeyUsageDaily).where(ApiKeyUsageDaily.api_key_id == key.id)
    ).scalar_one()
    assert rollup.request_count == 1
    assert rollup.error_count == 0


def test_a_refused_request_is_still_metered(client, db_session, tenant) -> None:
    key, token = _mint(
        db_session, tenant.organization.id, tenant.org_admin.user.id
    )
    response = client.get(
        f"{GATEWAY}/documents",
        params={"workspace_id": str(tenant.foreign_workspace.id)},
        headers=_auth(token),
    )
    assert response.status_code == status.HTTP_404_NOT_FOUND

    rollup = db_session.execute(
        select(ApiKeyUsageDaily).where(ApiKeyUsageDaily.api_key_id == key.id)
    ).scalar_one()
    assert rollup.request_count == 1
    assert rollup.error_count == 1
    assert rollup.throttled_count == 0


# ===========================================================================
# Query: tier-scaled ef_search reported on the response
# ===========================================================================


@pytest.mark.parametrize(
    "tier", [ApiRateTier.FREE, ApiRateTier.BUILDER, ApiRateTier.PRO]
)
def test_the_query_response_reports_the_tier_and_its_ef_search(
    client, db_session, tenant, tier
) -> None:
    _key, token = _mint(
        db_session, tenant.organization.id, tenant.org_admin.user.id, tier=tier
    )
    response = client.post(
        f"{GATEWAY}/query",
        json={
            "workspace_id": str(tenant.workspace.id),
            "query": "payment terms",
            "top_k": 3,
        },
        headers=_auth(token),
    )
    assert response.status_code == status.HTTP_200_OK
    body = response.json()
    assert body["tier"] == tier.value
    assert body["ef_search"] == ef_search_for(tier)


def test_query_rejects_an_empty_string(client, db_session, tenant) -> None:
    _key, token = _mint(
        db_session, tenant.organization.id, tenant.org_admin.user.id
    )
    response = client.post(
        f"{GATEWAY}/query",
        json={"workspace_id": str(tenant.workspace.id), "query": "   "},
        headers=_auth(token),
    )
    assert response.status_code in (
        status.HTTP_422_UNPROCESSABLE_ENTITY,
        status.HTTP_400_BAD_REQUEST,
    )


def test_top_k_is_capped_by_the_contract(client, db_session, tenant) -> None:
    _key, token = _mint(
        db_session, tenant.organization.id, tenant.org_admin.user.id
    )
    response = client.post(
        f"{GATEWAY}/query",
        json={
            "workspace_id": str(tenant.workspace.id),
            "query": "anything",
            "top_k": 5000,
        },
        headers=_auth(token),
    )
    assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY


# ===========================================================================
# Workflows
# ===========================================================================


def test_triggering_an_unknown_workflow_is_404(
    client, db_session, tenant
) -> None:
    _key, token = _mint(
        db_session, tenant.organization.id, tenant.org_admin.user.id
    )
    response = client.post(
        f"{GATEWAY}/workflows/{uuid.uuid4()}/trigger",
        json={
            "workspace_id": str(tenant.workspace.id),
            "work_item_id": str(uuid.uuid4()),
        },
        headers=_auth(token),
    )
    assert response.status_code == status.HTTP_404_NOT_FOUND


def test_the_trigger_route_is_declared_202_not_200() -> None:
    from app.api.v1.public import gateway

    routes = [
        route
        for route in gateway.router.routes
        if getattr(route, "name", None) == "trigger_workflow"
    ]
    assert routes, "trigger_workflow route not registered"
    assert routes[0].status_code == status.HTTP_202_ACCEPTED


# ===========================================================================
# Developer portal authorisation
# ===========================================================================


def test_an_api_key_cannot_manage_the_developer_portal(
    client, db_session, tenant
) -> None:
    _key, token = _mint(
        db_session,
        tenant.organization.id,
        tenant.org_admin.user.id,
        scopes=[
            ApiKeyScope.ORGANIZATIONS_READ.value,
            ApiKeyScope.PUBLIC_DOCUMENTS_READ.value,
        ],
    )
    response = client.get(
        f"/api/v1/organizations/{tenant.organization.id}/developer",
        headers=_auth(token),
    )
    assert response.status_code in (
        status.HTTP_401_UNAUTHORIZED,
        status.HTTP_403_FORBIDDEN,
    )


def test_an_admin_of_another_tenant_cannot_read_this_portal(
    client, tenant
) -> None:
    response = client.get(
        f"/api/v1/organizations/{tenant.organization.id}/developer",
        headers=tenant.other_org_member.headers,
    )
    assert response.status_code == status.HTTP_404_NOT_FOUND


def test_a_member_cannot_read_the_portal(client, tenant) -> None:
    response = client.get(
        f"/api/v1/organizations/{tenant.organization.id}/developer",
        headers=tenant.contributor.headers,
    )
    assert response.status_code == status.HTTP_403_FORBIDDEN


def test_an_admin_reads_the_portal_and_sees_the_plan_ceiling(
    client, db_session, tenant
) -> None:
    _mint(db_session, tenant.organization.id, tenant.org_admin.user.id)
    response = client.get(
        f"/api/v1/organizations/{tenant.organization.id}/developer",
        headers=tenant.org_admin.headers,
    )
    assert response.status_code == status.HTTP_200_OK
    body = response.json()
    catalogue = body["tier_catalogue"]
    assert catalogue["ceiling"] == ApiRateTier.FREE.value
    assert catalogue["quota_tier_key"] is None
    assert len(catalogue["tiers"]) == len(ApiRateTier)
    assignable = {t["key"] for t in catalogue["tiers"] if t["assignable"]}
    assert assignable == {ApiRateTier.FREE.value}


def test_raising_a_tier_above_the_ceiling_is_409(client, db_session, tenant) -> None:
    key, _token = _mint(
        db_session,
        tenant.organization.id,
        tenant.org_admin.user.id,
        tier=ApiRateTier.FREE,
    )
    response = client.patch(
        f"/api/v1/organizations/{tenant.organization.id}"
        f"/developer/keys/{key.id}/tier",
        json={"tier_key": ApiRateTier.ENTERPRISE.value},
        headers=tenant.org_admin.headers,
    )
    assert response.status_code == status.HTTP_409_CONFLICT
    assert "ceiling" in response.json()["detail"].lower()


def test_enabling_the_gateway_without_a_public_scope_is_refused(
    client, db_session, tenant
) -> None:
    key, _token = _mint(
        db_session,
        tenant.organization.id,
        tenant.org_admin.user.id,
        scopes=[ApiKeyScope.ORGANIZATIONS_READ.value],
        enabled=False,
    )
    response = client.patch(
        f"/api/v1/organizations/{tenant.organization.id}"
        f"/developer/keys/{key.id}/tier",
        json={"tier_key": ApiRateTier.FREE.value, "enable_public_api": True},
        headers=tenant.org_admin.headers,
    )
    assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY


def test_the_explorer_returns_snippets_in_three_languages(
    client, tenant
) -> None:
    response = client.get(
        f"/api/v1/organizations/{tenant.organization.id}/developer/explorer",
        headers=tenant.org_admin.headers,
    )
    assert response.status_code == status.HTTP_200_OK
    operations = response.json()["operations"]
    assert operations
    for operation in operations:
        snippets = operation["snippets"]
        assert set(snippets) == {"curl", "python", "typescript"}
        assert "$FLOWPILOT_API_KEY" in snippets["curl"]
        assert "FLOWPILOT_API_KEY" in snippets["python"]
        assert operation["required_scope"] in ALL_PUBLIC_SCOPES


def test_issuing_a_key_returns_the_token_exactly_once(
    client, db_session, tenant
) -> None:
    response = client.post(
        f"/api/v1/organizations/{tenant.organization.id}/developer/keys",
        json={
            "name": "integration",
            "scopes": ["public_documents:read"],
            "tier_key": "FREE",
            "enable_public_api": True,
        },
        headers=tenant.org_admin.headers,
    )
    assert response.status_code == status.HTTP_201_CREATED
    body = response.json()
    token = body["token"]
    assert token.startswith(("fp_live_", "fp_test_"))
    assert body["api_key"]["is_public_api_enabled"] is True
    assert body["api_key"]["tier_key"] == "FREE"

    key_id = body["api_key"]["id"]
    listing = client.get(
        f"/api/v1/organizations/{tenant.organization.id}/developer",
        headers=tenant.org_admin.headers,
    ).json()
    stored = next(k for k in listing["keys"] if k["id"] == key_id)
    assert token not in str(stored)
    assert "secret" not in str(stored).lower()


def test_key_metrics_report_no_data_as_null_not_zero(
    client, db_session, tenant
) -> None:
    key, _token = _mint(
        db_session, tenant.organization.id, tenant.org_admin.user.id
    )
    response = client.get(
        f"/api/v1/organizations/{tenant.organization.id}"
        f"/developer/keys/{key.id}/metrics",
        params={"days": 7},
        headers=tenant.org_admin.headers,
    )
    assert response.status_code == status.HTTP_200_OK
    body = response.json()
    assert body["total_requests"] == 0
    assert body["p50_latency_ms"] is None
    assert body["p95_latency_ms"] is None
    assert body["mean_latency_ms"] is None
    assert body["error_rate"] is None
    assert body["latency_method"] == "HISTOGRAM_INTERPOLATED"
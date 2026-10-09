"""Developer request log: GET /organizations/{id}/developer/requests.

The per-request view of the public gateway, read from the `usage_events`
ledger that `meter_request` already writes. Developer tier and above.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi import status

from tests.api.test_public_api_endpoints import GATEWAY, _auth, _mint


@pytest.fixture(autouse=True)
def developer_plan(db_session, tenant):
    from tests.security.plans import put_on_plan

    return put_on_plan(db_session, tenant.organization, "developer")


def _url(tenant) -> str:
    return f"/api/v1/organizations/{tenant.organization.id}/developer/requests"


def _call_gateway(client, tenant, token, *, workspace_id=None):
    return client.get(
        f"{GATEWAY}/documents",
        params={"workspace_id": str(workspace_id or tenant.workspace.id)},
        headers=_auth(token),
    )


def test_gateway_calls_appear_newest_first_with_status_and_key_name(
    client, db_session, tenant
) -> None:
    key, token = _mint(db_session, tenant.organization.id, tenant.org_admin.user.id)
    assert _call_gateway(client, tenant, token).status_code == 200
    assert _call_gateway(client, tenant, token, workspace_id=uuid.uuid4()).status_code == 404

    response = client.get(_url(tenant), headers=tenant.org_admin.headers)
    assert response.status_code == status.HTTP_200_OK, response.text
    items = response.json()["items"]
    assert [item["status_code"] for item in items] == [404, 200]
    first = items[0]
    assert first["api_key_id"] == str(key.id)
    assert first["api_key_name"] == key.name
    assert first["method"] == "GET"
    assert first["route"] and "documents" in first["route"]
    assert first["throttled"] is False


def test_outcome_filter_separates_errors_from_successes(
    client, db_session, tenant
) -> None:
    _key, token = _mint(db_session, tenant.organization.id, tenant.org_admin.user.id)
    _call_gateway(client, tenant, token)
    _call_gateway(client, tenant, token, workspace_id=uuid.uuid4())

    errors = client.get(
        _url(tenant), params={"outcome": "error"}, headers=tenant.org_admin.headers
    ).json()["items"]
    successes = client.get(
        _url(tenant), params={"outcome": "success"}, headers=tenant.org_admin.headers
    ).json()["items"]
    assert [i["status_code"] for i in errors] == [404]
    assert [i["status_code"] for i in successes] == [200]


def test_key_filter_and_keyset_pagination(client, db_session, tenant) -> None:
    key_a, token_a = _mint(db_session, tenant.organization.id, tenant.org_admin.user.id)
    _key_b, token_b = _mint(db_session, tenant.organization.id, tenant.org_admin.user.id)
    for _ in range(3):
        _call_gateway(client, tenant, token_a)
    _call_gateway(client, tenant, token_b)

    seen: list[str] = []
    cursor = None
    while True:
        params = {"api_key_id": str(key_a.id), "limit": 2}
        if cursor:
            params["cursor"] = cursor
        body = client.get(_url(tenant), params=params, headers=tenant.org_admin.headers).json()
        assert all(i["api_key_id"] == str(key_a.id) for i in body["items"])
        seen += [i["id"] for i in body["items"]]
        cursor = body["next_cursor"]
        if not cursor:
            break
    assert len(seen) == 3 and len(set(seen)) == 3


def test_a_tampered_cursor_is_400_not_500(client, tenant) -> None:
    response = client.get(
        _url(tenant), params={"cursor": "not-a-cursor"}, headers=tenant.org_admin.headers
    )
    assert response.status_code == status.HTTP_400_BAD_REQUEST


def test_an_unknown_outcome_is_rejected(client, tenant) -> None:
    response = client.get(
        _url(tenant), params={"outcome": "everything"}, headers=tenant.org_admin.headers
    )
    assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY


def test_the_log_is_gated_to_the_developer_tier(client, db_session, tenant) -> None:
    from tests.security.plans import put_on_plan

    put_on_plan(db_session, tenant.organization, "free")
    response = client.get(_url(tenant), headers=tenant.org_admin.headers)
    assert response.status_code in (
        status.HTTP_402_PAYMENT_REQUIRED,
        status.HTTP_403_FORBIDDEN,
    )


def test_a_member_cannot_read_the_log(client, tenant) -> None:
    response = client.get(_url(tenant), headers=tenant.contributor.headers)
    assert response.status_code == status.HTTP_403_FORBIDDEN


def test_an_admin_of_another_tenant_cannot_read_the_log(client, tenant) -> None:
    response = client.get(_url(tenant), headers=tenant.other_org_member.headers)
    assert response.status_code == status.HTTP_404_NOT_FOUND


def test_an_api_key_cannot_read_the_log(client, db_session, tenant) -> None:
    _key, token = _mint(db_session, tenant.organization.id, tenant.org_admin.user.id)
    response = client.get(_url(tenant), headers=_auth(token))
    assert response.status_code in (
        status.HTTP_401_UNAUTHORIZED,
        status.HTTP_403_FORBIDDEN,
    )

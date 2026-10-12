"""A person chooses which notification emails they get (campaign session 3, N-062).

`GET /me/notification-preferences` lists every category with whether it is emailed and whether
it can be changed; `PUT` changes the optional ones. Security and Billing are locked on: refusing
to turn them off is the answer, not a silent no-op that shows "off" and keeps sending.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from tests.conftest import Fixture

URL = "/api/v1/me/notification-preferences"


def _by_key(body: dict) -> dict[str, dict]:
    return {row["key"]: row for row in body["categories"]}


def test_every_category_is_listed_and_on_by_default(client: TestClient, tenant: Fixture) -> None:
    response = client.get(URL, headers=tenant.viewer.headers)
    assert response.status_code == 200, response.text
    rows = _by_key(response.json())
    assert list(rows) == ["security", "billing", "usage", "workflow"]
    assert all(row["email"] is True for row in rows.values())
    assert rows["security"]["locked"] and rows["billing"]["locked"]
    assert not rows["usage"]["locked"] and not rows["workflow"]["locked"]
    assert all(row["label"] and row["description"] for row in rows.values())


def test_an_optional_category_can_be_turned_off_and_on_again(client: TestClient, tenant: Fixture) -> None:
    off = client.put(URL, json={"category": "workflow", "email": False}, headers=tenant.contributor.headers)
    assert off.status_code == 200, off.text
    assert _by_key(off.json())["workflow"]["email"] is False
    assert _by_key(client.get(URL, headers=tenant.contributor.headers).json())["workflow"]["email"] is False
    # Each person's choice is their own.
    assert _by_key(client.get(URL, headers=tenant.viewer.headers).json())["workflow"]["email"] is True

    on = client.put(URL, json={"category": "workflow", "email": True}, headers=tenant.contributor.headers)
    assert _by_key(on.json())["workflow"]["email"] is True


def test_security_and_billing_cannot_be_turned_off(client: TestClient, tenant: Fixture) -> None:
    for key in ("security", "billing"):
        response = client.put(URL, json={"category": key, "email": False}, headers=tenant.owner.headers)
        assert response.status_code == 422, response.text
        assert "cannot be turned off" in response.text
    assert all(row["email"] for row in client.get(URL, headers=tenant.owner.headers).json()["categories"])


def test_an_unknown_category_or_extra_field_is_refused(client: TestClient, tenant: Fixture) -> None:
    assert client.put(URL, json={"category": "marketing", "email": False}, headers=tenant.owner.headers).status_code == 422
    assert client.put(
        URL, json={"category": "usage", "email": False, "user_id": str(tenant.viewer.user.id)}, headers=tenant.owner.headers
    ).status_code == 422


def test_signed_out_is_refused(client: TestClient) -> None:
    assert client.get(URL).status_code == 401

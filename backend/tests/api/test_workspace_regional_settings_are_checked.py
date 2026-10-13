"""A workspace's regional settings are only what the server can honour (campaign session 3, F-320).

A workspace carries a clock (`timezone`), a fallback `currency` for three-way matching, a
`date_format` for dates on documents, and a `language`. The server stored whatever it was sent:
`"timezone": "Not/AZone"` was saved and shown back, while obligation due dates quietly fell back
to UTC and an export schedule following the workspace clock could no longer be computed. The
person's own profile already refused an unknown zone; the workspace (and the first workspace an
organization is created with) did not.

Now each value is checked where it enters: a real IANA zone, a three-letter currency code, one of
the date formats the product renders, and a language tag. Anything else is a 422 naming the field.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from tests.conftest import Fixture

API = "/api/v1"

BAD = [
    ("timezone", "Not/AZone"),
    ("timezone", "GMT+5:30"),
    ("currency", "DOLLARS"),
    ("currency", "U$"),
    ("date_format", "DD.MM.YY"),
    ("language", "english please"),
]


@pytest.mark.parametrize(("field", "value"), BAD)
def test_a_workspace_refuses_a_value_it_cannot_honour(client: TestClient, tenant: Fixture, field, value) -> None:
    url = f"{API}/workspaces/{tenant.workspace.id}"
    before = client.get(url, headers=tenant.owner.headers).json()[field]
    response = client.patch(url, json={field: value}, headers=tenant.owner.headers)
    assert response.status_code == 422, (field, value, response.text)
    assert field in response.text
    assert client.get(url, headers=tenant.owner.headers).json()[field] == before


def test_a_workspace_takes_any_real_zone_currency_and_language(client: TestClient, tenant: Fixture) -> None:
    url = f"{API}/workspaces/{tenant.workspace.id}"
    saved = client.patch(url, json={
        "timezone": "America/Sao_Paulo", "currency": "chf", "language": "pt-BR", "date_format": "DD/MM/YYYY",
    }, headers=tenant.owner.headers)
    assert saved.status_code == 200, saved.text
    body = saved.json()
    assert (body["timezone"], body["currency"], body["language"], body["date_format"]) == (
        "America/Sao_Paulo", "CHF", "pt-BR", "DD/MM/YYYY",
    )


@pytest.mark.parametrize(("field", "value"), BAD)
def test_a_new_workspace_refuses_the_same_values(client: TestClient, tenant: Fixture, field, value) -> None:
    response = client.post(
        f"{API}/organizations/{tenant.organization.id}/workspaces",
        json={"workspace_name": "Regional check", field: value},
        headers=tenant.owner.headers,
    )
    assert response.status_code == 422, (field, value, response.text)


@pytest.mark.parametrize(("field", "value"), BAD)
def test_a_new_organizations_first_workspace_refuses_them_too(client: TestClient, tenant: Fixture, field, value) -> None:
    response = client.post(
        f"{API}/organizations",
        json={"organization_name": "Regional Check Ltd", field: value},
        headers=tenant.non_member.headers,
    )
    assert response.status_code == 422, (field, value, response.text)
    assert field in response.text


def test_a_new_organization_with_real_values_is_created(client: TestClient, tenant: Fixture) -> None:
    response = client.post(
        f"{API}/organizations",
        json={"organization_name": "Regional Check GmbH", "timezone": "Europe/Zurich", "currency": "CHF",
              "language": "de", "date_format": "DD-MM-YYYY"},
        headers=tenant.non_member.headers,
    )
    assert response.status_code == 201, response.text

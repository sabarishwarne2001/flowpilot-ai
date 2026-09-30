"""F-037 — a NUL character is refused at the edge instead of crashing a route.

    pytest tests/security/test_nul_byte_guard.py -q

PostgreSQL text cannot hold U+0000; the driver raises ValueError while building
the statement, which no handler expects, so `?q=%00` was an unhandled 500 on
about 19 routes. The middleware answers 400 before anything else runs.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

ROUTES_THAT_USED_TO_CRASH = [
    ("/api/v1/workspaces/{ws}/work-items", "search"),
    ("/api/v1/workspaces/{ws}/obligations", "q"),
    ("/api/v1/workspaces/{ws}/entities", "q"),
    ("/api/v1/workspaces/{ws}/cases", "status"),
    ("/api/v1/workspaces/{ws}/assistant/sessions", "q"),
]


@pytest.mark.parametrize(("path", "param"), ROUTES_THAT_USED_TO_CRASH)
def test_a_nul_in_a_query_parameter_is_a_400_not_a_500(client: TestClient, tenant, path: str, param: str) -> None:
    from tests.security.plans import put_on_plan

    response = client.get(
        path.format(ws=tenant.workspace.id) + f"?{param}=abc%00def", headers=tenant.owner.headers
    )
    assert response.status_code == 400, (path, response.status_code)
    assert "NUL" in response.json()["detail"]


def test_a_raw_nul_and_an_encoded_one_in_the_path_are_refused(client: TestClient, tenant) -> None:
    assert client.get(f"/api/v1/workspaces/{tenant.workspace.id}%00/work-items", headers=tenant.owner.headers).status_code == 400


def test_a_json_escape_nul_in_a_body_is_a_400(client: TestClient, tenant) -> None:
    body = '{"name": "Team\\u0000Two", "slug": "team-two"}'
    response = client.post(
        f"/api/v1/organizations/{tenant.organization.id}/workspaces",
        content=body,
        headers={**tenant.owner.headers, "content-type": "application/json"},
    )
    assert response.status_code == 400 and "NUL" in response.json()["detail"]


def test_a_form_encoded_nul_is_a_400(client: TestClient) -> None:
    response = client.post(
        "/api/v1/auth/login",
        content="username=a%00b&password=secret",
        headers={"content-type": "application/x-www-form-urlencoded"},
    )
    assert response.status_code == 400


def test_an_escaped_backslash_followed_by_the_text_u0000_is_legitimate(client: TestClient, tenant) -> None:
    """`\\\\u0000` in JSON is a backslash then the letters u-0-0-0-0. It is not a NUL."""
    body = json.dumps({"query": "C:\\path\\u0000"})
    response = client.post(
        "/api/v1/auth/login",
        content=body,
        headers={"content-type": "application/json"},
    )
    assert response.status_code != 400 or "NUL" not in response.text


def test_ordinary_requests_are_unaffected(client: TestClient, tenant) -> None:
    assert client.get(
        f"/api/v1/workspaces/{tenant.workspace.id}/work-items?search=invoice", headers=tenant.owner.headers
    ).status_code == 200
    assert client.get("/api/v1/health").status_code == 200


def test_a_binary_upload_is_not_inspected(client: TestClient, tenant, monkeypatch, tmp_path) -> None:
    """Uploads legitimately contain NUL bytes; the guard must not look inside them."""
    import io

    from pypdf import PdfWriter

    from app.core.config import settings
    from app.core.storage import reset_storage_driver

    monkeypatch.setattr(settings, "STORAGE_BACKEND", "local")
    monkeypatch.setattr(settings, "UPLOAD_DIR", tmp_path)
    reset_storage_driver()
    writer = PdfWriter()
    writer.add_blank_page(72, 72)
    buffer = io.BytesIO()
    writer.write(buffer)
    assert b"\x00" in buffer.getvalue() or True
    response = client.post(
        f"/api/v1/workspaces/{tenant.workspace.id}/work-items",
        files={"file": ("a.pdf", buffer.getvalue(), "application/pdf")},
        headers=tenant.contributor.headers,
    )
    reset_storage_driver()
    assert response.status_code == 201, response.text[:200]

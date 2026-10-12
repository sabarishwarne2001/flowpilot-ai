"""Logos and favicons cannot carry script (campaign session 3, security review).

Avatars and documents were already proven (test_avatar_upload, test_upload_attacks). The workspace
logo, the organization's branding logo and its favicon are shown to other people (and the branding
ones on public pages), so an SVG with a script, or a PNG with an HTML page appended (a polyglot), is
the classic stored-XSS route. Each must be refused or re-encoded into a plain PNG served with
`nosniff`.
"""

from __future__ import annotations

import io

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy.orm import Session

from tests.conftest import Fixture
from tests.security.plans import put_on_plan

SVG = b'<svg xmlns="http://www.w3.org/2000/svg" onload="alert(1)"><script>alert(2)</script></svg>'
HTML_TAIL = b"<html><script>alert(document.cookie)</script></html>"


def _png() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (64, 64), (10, 120, 200)).save(buffer, format="PNG")
    return buffer.getvalue()


def _targets(tenant: Fixture) -> list[tuple[str, str]]:
    org, ws = tenant.organization.id, tenant.workspace.id
    return [
        (f"/api/v1/workspaces/{ws}/upload/logo", f"/api/v1/workspaces/{ws}/logo"),
        (f"/api/v1/organizations/{org}/branding/logo", f"/api/v1/organizations/{org}/branding/logo"),
        (f"/api/v1/organizations/{org}/branding/favicon", f"/api/v1/organizations/{org}/branding/favicon"),
    ]


@pytest.fixture
def branded(db_session: Session, tenant: Fixture) -> Fixture:
    put_on_plan(db_session, tenant.organization, "enterprise")
    return tenant


def test_an_svg_with_script_is_refused(client: TestClient, branded: Fixture) -> None:
    for upload, _ in _targets(branded):
        for name, ctype in (("logo.svg", "image/svg+xml"), ("logo.png", "image/png")):
            response = client.post(upload, files={"file": (name, SVG, ctype)}, headers=branded.owner.headers)
            assert response.status_code in (400, 415, 422), (upload, name, response.status_code, response.text)


def test_a_png_with_a_page_appended_is_stored_as_a_plain_png(client: TestClient, branded: Fixture) -> None:
    polyglot = _png() + HTML_TAIL
    for upload, serve in _targets(branded):
        response = client.post(upload, files={"file": ("logo.png", polyglot, "image/png")}, headers=branded.owner.headers)
        assert response.status_code in (200, 201), (upload, response.status_code, response.text)
        served = client.get(serve, headers=branded.owner.headers)
        assert served.status_code == 200, (serve, served.text[:200])
        assert served.headers["content-type"].startswith("image/png")
        assert served.headers.get("x-content-type-options") == "nosniff"
        assert b"<script>" not in served.content and b"<html>" not in served.content
        assert served.content.startswith(b"\x89PNG")

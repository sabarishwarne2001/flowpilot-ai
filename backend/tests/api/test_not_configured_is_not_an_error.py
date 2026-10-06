"""F-058 — a normal "nothing configured yet" state is not answered with a 404.

Two healthy console pages logged an error on every visit:

* Settings -> Email asked for the workspace email override and got 404 "This
  workspace has no email override" (the normal state of most workspaces).
* Organization -> Branding previewed the logo from the PUBLIC /branding/logo,
  which resolves the tenant only from a verified custom domain, so on the app's
  own host it was a 404 even when a logo was uploaded.

Now the override read answers 200 null, and the console previews its own logo
and favicon through an organization-scoped, admin-only read.
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from PIL import Image

from app.core.config import settings
from app.core.storage import reset_storage_driver
from tests.security.plans import put_on_plan

API = "/api/v1"


@pytest.fixture()
def local_storage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "STORAGE_BACKEND", "local")
    monkeypatch.setattr(settings, "UPLOAD_DIR", tmp_path)
    reset_storage_driver()
    yield tmp_path
    reset_storage_driver()


def _png() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (64, 64), (10, 120, 200)).save(buffer, format="PNG")
    return buffer.getvalue()


def test_a_workspace_without_an_email_override_reads_null(client, tenant) -> None:
    response = client.get(
        f"{API}/workspaces/{tenant.workspace.id}/email-settings",
        headers=tenant.ws_admin.headers,
    )
    assert response.status_code == 200, response.text
    assert response.json() is None


def test_the_console_previews_its_own_logo_on_the_app_host(client, db_session, tenant, local_storage) -> None:
    put_on_plan(db_session, tenant.organization, "developer")
    base = f"{API}/organizations/{tenant.organization.id}/branding"

    assert client.get(f"{base}/logo", headers=tenant.org_admin.headers).status_code == 404

    uploaded = client.post(
        f"{base}/logo", files={"file": ("logo.png", _png(), "image/png")}, headers=tenant.org_admin.headers
    )
    assert uploaded.status_code == 200, uploaded.text

    preview = client.get(f"{base}/logo", headers=tenant.org_admin.headers)
    assert preview.status_code == 200, preview.text
    assert preview.headers["content-type"] == "image/png"
    assert preview.headers["x-content-type-options"] == "nosniff"
    with Image.open(io.BytesIO(preview.content)) as image:
        assert image.size[0] > 0

    # The public route still answers only for a verified custom domain.
    assert client.get(f"{API}/branding/logo").status_code == 404


def test_the_preview_is_admin_only_and_tenant_scoped(client, db_session, tenant, local_storage) -> None:
    put_on_plan(db_session, tenant.organization, "developer")
    base = f"{API}/organizations/{tenant.organization.id}/branding"
    client.post(f"{base}/logo", files={"file": ("logo.png", _png(), "image/png")}, headers=tenant.org_admin.headers)

    assert client.get(f"{base}/logo", headers=tenant.contributor.headers).status_code == 403
    assert client.get(f"{base}/logo", headers=tenant.other_org_member.headers).status_code in (403, 404)
    assert client.get(f"{base}/favicon", headers=tenant.org_admin.headers).status_code == 404


@pytest.mark.parametrize("enabled", [False, True])
def test_branding_says_whether_custom_domains_are_served(client, db_session, tenant, monkeypatch, enabled) -> None:
    """F-060: the console offered "Claim domain" where the deployment has custom
    domains switched off, and the click answered 501. The branding read now
    carries the deployment's switch so the page can hide the button."""
    monkeypatch.setattr(settings, "CUSTOM_DOMAINS_ENABLED", enabled)
    put_on_plan(db_session, tenant.organization, "developer")
    response = client.get(
        f"{API}/organizations/{tenant.organization.id}/branding", headers=tenant.org_admin.headers
    )
    assert response.status_code == 200, response.text
    assert response.json()["custom_domains_enabled"] is enabled

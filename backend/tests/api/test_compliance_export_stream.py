"""F-138 — a DPA/GDPR export bundle can be downloaded on storage that cannot presign URLs.

Generating the bundle worked; downloading it answered 409 "The storage backend
for this region cannot mint presigned URLs." on local-disk storage, so the
button failed in development and test setups (production refuses local storage,
S3/R2/MinIO presign). The API now streams the archive itself in that case:
owners and admins only, scoped to the organization, audited like a presigned
download.
"""

from __future__ import annotations

import io
import zipfile

import pytest

from app.core.config import settings

pytestmark = pytest.mark.usefixtures("test_database")


def base(organization_id) -> str:
    return f"/api/v1/organizations/{organization_id}/compliance"


@pytest.fixture()
def local_storage(monkeypatch, tmp_path):
    from app.core.storage import reset_storage_driver

    monkeypatch.setattr(settings, "STORAGE_BACKEND", "local")
    monkeypatch.setattr(settings, "UPLOAD_DIR", str(tmp_path))
    reset_storage_driver()
    yield tmp_path
    reset_storage_driver()


def test_a_bundle_on_local_storage_downloads_through_the_api(client, tenant, local_storage):
    created = client.post(f"{base(tenant.organization.id)}/exports", headers=tenant.owner.headers)
    assert created.status_code == 201, created.text
    export_id = created.json()["id"]

    minted = client.get(f"{base(tenant.organization.id)}/exports/{export_id}/download", headers=tenant.owner.headers)
    assert minted.status_code == 200, minted.text
    assert minted.json()["delivery"] == "STREAM"

    archive = client.get(f"{base(tenant.organization.id)}/exports/{export_id}/archive", headers=tenant.owner.headers)
    assert archive.status_code == 200, archive.text
    assert "attachment" in archive.headers["content-disposition"]
    assert archive.headers.get("cache-control") == "no-store"
    with zipfile.ZipFile(io.BytesIO(archive.content)) as bundle:
        assert bundle.namelist(), "the archive is empty"


def test_the_archive_is_for_owners_and_admins_of_that_organization_only(client, tenant, local_storage):
    export_id = client.post(f"{base(tenant.organization.id)}/exports", headers=tenant.owner.headers).json()["id"]
    url = f"{base(tenant.organization.id)}/exports/{export_id}/archive"

    assert client.get(url, headers=tenant.contributor.headers).status_code == 403
    assert client.get(url, headers=tenant.other_org_member.headers).status_code in (403, 404)
    assert client.get(url, headers=tenant.org_admin.headers).status_code == 200

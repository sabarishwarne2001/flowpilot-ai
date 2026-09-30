"""File handling — hostile filenames through the real upload and download endpoints.

    pytest tests/security/test_upload_endpoint_filenames.py -q

The storage key of an upload is generated (organization id, then a random id),
never taken from the client. These tests upload a real PDF under hostile names
and check what is stored, where it lands on disk and what the download says.
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from pypdf import PdfWriter
from sqlalchemy import select

from app.core.config import settings
from app.core.storage import reset_storage_driver
from app.models.work_item import WorkItem


def _pdf() -> bytes:
    writer = PdfWriter()
    writer.add_blank_page(72, 72)
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


@pytest.fixture()
def local_storage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "STORAGE_BACKEND", "local")
    monkeypatch.setattr(settings, "UPLOAD_DIR", tmp_path)
    reset_storage_driver()
    yield tmp_path
    reset_storage_driver()


HOSTILE_NAMES = [
    "../../../etc/passwd.pdf",
    "..\\..\\windows\\system32\\evil.pdf",
    "/etc/cron.d/backdoor.pdf",
    "C:\\Users\\Public\\x.pdf",
    "a" * 400 + ".pdf",
    "invoice\x00.exe.pdf",
    "<img src=x onerror=alert(1)>.pdf",
    "x\r\nSet-Cookie: pwned=1\r\n.pdf",
    "\u202egpj.exe.pdf",
    "..pdf",
    ".pdf",
]


@pytest.mark.parametrize("name", HOSTILE_NAMES)
def test_a_hostile_filename_never_decides_where_the_file_is_stored(client, tenant, local_storage, db_session, name) -> None:
    response = client.post(
        f"/api/v1/workspaces/{tenant.workspace.id}/work-items",
        files={"file": (name, _pdf(), "application/pdf")},
        headers=tenant.contributor.headers,
    )
    if response.status_code != 201:
        # Refusing an unusable name is fine; crashing or writing somewhere else is not.
        assert response.status_code in (400, 413, 415, 422), (name, response.status_code, response.text[:200])
        return

    item = db_session.execute(select(WorkItem).order_by(WorkItem.created_at.desc())).scalars().first()
    key = item.stored_filename
    assert ".." not in key and not key.startswith(("/", "\\")) and "\x00" not in key and "\\" not in key
    assert key.startswith(str(tenant.organization.id)), key

    root = local_storage.resolve()
    written = [path.resolve() for path in root.rglob("*") if path.is_file()]
    assert written, "the upload should have produced a file under the storage root"
    assert all(root in path.parents for path in written), written
    # Nothing was created outside the storage root by the hostile name.
    assert not Path("/etc/cron.d/backdoor.pdf").exists()

    download = client.get(
        f"/api/v1/workspaces/{tenant.workspace.id}/work-items/{item.id}/content",
        headers=tenant.contributor.headers,
    )
    assert download.status_code in (200, 302, 307), download.status_code
    # The name may appear (percent-encoded) inside Content-Disposition, but it must
    # not have created a header of its own or carried a raw line break.
    assert "set-cookie" not in {key.lower() for key in download.headers.keys()}
    assert "\r" not in "".join(download.headers.values()) and "\n" not in "".join(download.headers.values())


def test_a_non_pdf_body_under_a_pdf_name_leaves_no_work_item_and_no_stored_file(
    client, tenant, local_storage, db_session
) -> None:
    response = client.post(
        f"/api/v1/workspaces/{tenant.workspace.id}/work-items",
        files={"file": ("invoice.pdf", b"<html><script>alert(1)</script></html>", "application/pdf")},
        headers=tenant.contributor.headers,
    )
    assert response.status_code == 400
    assert db_session.execute(select(WorkItem)).first() is None
    stored = [p for p in local_storage.rglob("*") if p.is_file() and "quarantine" not in p.parts]
    assert stored == []


def test_an_upload_over_the_limit_is_413_and_stores_nothing(client, tenant, local_storage, db_session, monkeypatch) -> None:
    monkeypatch.setattr(settings, "MAX_UPLOAD_SIZE", 1024 * 1024)
    big = _pdf() + b"\n%" + b"A" * (2 * 1024 * 1024)
    response = client.post(
        f"/api/v1/workspaces/{tenant.workspace.id}/work-items",
        files={"file": ("big.pdf", big, "application/pdf")},
        headers=tenant.contributor.headers,
    )
    assert response.status_code == 413
    assert db_session.execute(select(WorkItem)).first() is None


def test_a_viewer_cannot_upload(client, tenant, local_storage, db_session) -> None:
    response = client.post(
        f"/api/v1/workspaces/{tenant.workspace.id}/work-items",
        files={"file": ("a.pdf", _pdf(), "application/pdf")},
        headers=tenant.viewer.headers,
    )
    assert response.status_code == 403
    assert db_session.execute(select(WorkItem)).first() is None

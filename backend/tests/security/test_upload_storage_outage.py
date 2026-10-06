"""F-026 — a storage outage during an upload is a 503, and nothing is saved.

When the object store refused the write (MinIO credentials wrong, the bucket
unreachable), POST /work-items answered a bare 500 with a StorageError
traceback in the log. The file is written before any database row, so nothing
was half-created; the user just got an error that looks like a bug instead of
"try again in a moment".
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import select

from app.core.storage import ObjectNotFoundError, StorageError, get_storage_driver
from app.models.uploaded_file import UploadedFile
from app.models.work_item import WorkItem
from tests.security.test_upload_endpoint_filenames import _pdf, local_storage  # noqa: F401


def _refuse(*_args, **_kwargs):
    raise StorageError("InvalidAccessKeyId: The Access Key Id you provided does not exist")


def test_a_storage_outage_is_503_and_creates_nothing(
    client, tenant, local_storage: Path, db_session, monkeypatch: pytest.MonkeyPatch  # noqa: F811
) -> None:
    driver = get_storage_driver()
    monkeypatch.setattr(type(driver), "put_stream", _refuse)

    response = client.post(
        f"/api/v1/workspaces/{tenant.workspace.id}/work-items",
        files={"file": ("invoice.pdf", _pdf(), "application/pdf")},
        headers=tenant.contributor.headers,
    )

    assert response.status_code == 503, response.text
    body = response.json()
    assert body["code"] == "STORAGE_UNAVAILABLE"
    assert "nothing was saved" in body["detail"].lower()
    assert "InvalidAccessKeyId" not in response.text, "the storage error text is internal"
    assert response.headers.get("retry-after")
    assert db_session.execute(select(WorkItem)).first() is None
    assert db_session.execute(select(UploadedFile)).first() is None


def test_a_missing_stored_object_is_404_not_503() -> None:
    from app.main import _storage_error_status

    assert _storage_error_status(ObjectNotFoundError("gone")) == 404
    assert _storage_error_status(StorageError("down")) == 503

"""F-158 / F-159, live: what an upload records about itself.

F-158: Document settings offer "Duplicate detection" (on by default: "detect and flag potential
       duplicate documents during upload"), and nothing read it: five copies of one file uploaded
       in the same second became five unrelated documents. A copy now names the original.
F-159: a filename longer than 255 characters was cut at 255 and lost its extension.

Two copies uploaded at the same moment are serialised by an advisory lock; that path needs two real
connections, so it is proven against the running server (FINDINGS F-158), not here.
"""

from __future__ import annotations

from sqlalchemy import select

from app.models.document_settings import DocumentSettings
from app.models.work_item import WorkItem
from tests.engines.conftest import Engines, make_pdf


def _upload(engines: Engines, name: str, data: bytes, *, workspace_id=None) -> dict:
    url = f"/api/v1/workspaces/{workspace_id or engines.ws}/work-items"
    response = engines.client.post(
        url, files={"file": (name, data, "application/pdf")}, headers=engines.tenant.owner.headers
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_a_second_copy_names_the_first(engines: Engines) -> None:
    data = make_pdf([["DUPLICATE PROBE ONE", "Total: 10.00"]])
    first = _upload(engines, "first.pdf", data)
    second = _upload(engines, "second-copy.pdf", data)
    other = _upload(engines, "other.pdf", make_pdf([["A DIFFERENT DOCUMENT"]]))

    assert first["duplicate_of"] is None
    assert second["duplicate_of"] == {"id": first["id"], "original_filename": "first.pdf"}
    assert other["duplicate_of"] is None

    listed = engines.get("/work-items", params={"search": "second-copy"}).json()["items"]
    assert listed[0]["duplicate_of"]["id"] == first["id"]
    detail = engines.get(f"/work-items/{second['id']}").json()
    assert detail["duplicate_of"]["original_filename"] == "first.pdf"


def test_the_setting_off_records_nothing(engines: Engines) -> None:
    data = make_pdf([["DUPLICATE PROBE SETTING"]])
    _upload(engines, "a.pdf", data)
    settings = engines.db.execute(
        select(DocumentSettings).where(DocumentSettings.workspace_id == engines.ws)
    ).scalar_one_or_none()
    if settings is None:
        response = engines.get("/document-settings/")
        assert response.status_code == 200, response.text
        engines.refresh()
        settings = engines.db.execute(
            select(DocumentSettings).where(DocumentSettings.workspace_id == engines.ws)
        ).scalar_one()
    settings.duplicate_detection = False
    engines.refresh()

    assert _upload(engines, "b.pdf", data)["duplicate_of"] is None


def test_a_deleted_original_does_not_count(engines: Engines) -> None:
    data = make_pdf([["DUPLICATE PROBE SCOPE"]])
    original = _upload(engines, "original.pdf", data)

    deleted = engines.delete(f"/work-items/{original['id']}")
    assert deleted.status_code in (200, 204), deleted.text
    assert _upload(engines, "after-delete.pdf", data)["duplicate_of"] is None


def test_deleting_the_original_clears_the_reference(engines: Engines) -> None:
    data = make_pdf([["DUPLICATE PROBE CLEAR"]])
    original = _upload(engines, "keep.pdf", data)
    copy = _upload(engines, "copy.pdf", data)
    assert copy["duplicate_of"]["id"] == original["id"]

    assert engines.delete(f"/work-items/{original['id']}").status_code in (200, 204)

    engines.refresh()
    row = engines.db.execute(select(WorkItem).where(WorkItem.id == copy["id"])).scalar_one()
    assert row.duplicate_of_work_item_id is None


def test_a_long_filename_keeps_its_extension(engines: Engines) -> None:
    name = "q" * 300 + ".pdf"
    created = _upload(engines, name, make_pdf([["LONG NAME PROBE"]]))

    assert len(created["original_filename"]) == 255
    assert created["original_filename"].endswith(".pdf")

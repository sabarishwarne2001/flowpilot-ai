"""Deleting a document deletes its file (F-151).

The confirmation says "This will permanently delete the uploaded file, extracted data,
embeddings, and all related processing history". The single and the bulk delete removed the
work item row only: the original PDF stayed in storage and its `uploaded_files` record stayed
live, so the file a customer deleted was still held (and still counted as theirs).

A file a supplier invoice keeps as its source document is a financial record and stays, as in
the subject erasure (PRESERVED_FINANCIAL_TABLES); its record is still marked deleted.
"""

from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import select

from app.core.storage import get_storage_driver
from app.models.uploaded_file import UploadedFile
from tests.engines.conftest import Engines

PAGE = ["DELIVERY NOTE", "Twelve pallets of copier paper delivered to dock 4."]


def _file(engines: Engines, work_item_id: uuid.UUID) -> UploadedFile:
    item = engines.item(work_item_id)
    assert item.uploaded_file_id is not None
    engines.refresh()
    return engines.db.execute(
        select(UploadedFile).where(UploadedFile.id == item.uploaded_file_id)
    ).scalar_one()


def test_deleting_a_document_deletes_its_stored_file(engines: Engines) -> None:
    work_item_id = engines.process("delivery-note.pdf", [PAGE])
    record = _file(engines, work_item_id)
    file_id, key = record.id, record.file_path
    driver = get_storage_driver()
    assert driver.exists(key)

    response = engines.delete(f"/work-items/{work_item_id}")
    assert response.status_code == 204, response.text

    assert not driver.exists(key), "the deleted document's file is still in storage"
    engines.refresh()
    released = engines.db.execute(select(UploadedFile).where(UploadedFile.id == file_id)).scalar_one()
    assert released.deleted_at is not None


def test_a_bulk_delete_deletes_the_files_too(engines: Engines) -> None:
    first = engines.process("note-1.pdf", [PAGE])
    second = engines.process("note-2.pdf", [PAGE + ["Second delivery."]])
    keys = [_file(engines, first).file_path, _file(engines, second).file_path]

    response = engines.post(
        "/work-items/bulk",
        {"action": "delete", "ids": [str(first), str(second)], "idempotency_key": uuid.uuid4().hex},
    )
    assert response.status_code == 200, response.text
    assert response.json()["succeeded"] == 2

    driver = get_storage_driver()
    assert [driver.exists(key) for key in keys] == [False, False]


def test_a_file_a_supplier_invoice_keeps_is_not_destroyed(engines: Engines) -> None:
    from app.models.supplier_cogs import SupplierInvoice

    work_item_id = engines.process("supplier-statement.pdf", [PAGE])
    record = _file(engines, work_item_id)
    key = record.file_path
    engines.db.add(
        SupplierInvoice(
            provider="groq",
            period_start=date(2026, 9, 1),
            period_end=date(2026, 9, 30),
            invoiced_total_micros=10_000_000,
            raw_document_file_id=record.id,
        )
    )
    engines.db.commit()

    assert engines.delete(f"/work-items/{work_item_id}").status_code == 204
    assert get_storage_driver().exists(key), "a financial record's source document was destroyed"

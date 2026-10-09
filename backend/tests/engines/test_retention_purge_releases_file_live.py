"""The retention auto-purge deletes a document's file, as a person's delete does (F-183).

An organization that sets "Documents: 90 days" with auto-purge on is told that older documents
are removed. The nightly sweep (`scripts/sweep_compliance.py --purge --apply`) deleted the
work-item rows with SQL of its own and nothing else: the original PDF stayed in storage and its
`uploaded_files` record stayed live, so the data the policy promised to remove was still held.
F-151 fixed exactly this for the delete button; the purge path never went through that code.

The document here is fully processed (chunks, extraction, the derived rows a real one has), so
the test also proves the purge's DELETE is not refused by any table that points at it.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select, text
from sqlalchemy.orm import sessionmaker

from app.core.storage import get_storage_driver
from app.models.compliance import RetentionPolicy
from app.models.uploaded_file import UploadedFile
from app.models.work_item import WorkItem
from tests.engines.conftest import Engines

PAGE = ["DELIVERY NOTE", "Twelve pallets of copier paper delivered to dock 4."]


def test_the_retention_purge_deletes_the_purged_documents_files(engines: Engines) -> None:
    from scripts import sweep_compliance

    work_item_id = engines.process("old-delivery-note.pdf", [PAGE])
    item = engines.item(work_item_id)
    assert item.uploaded_file_id is not None
    record = engines.db.execute(
        select(UploadedFile).where(UploadedFile.id == item.uploaded_file_id)
    ).scalar_one()
    file_id, key = record.id, record.file_path
    driver = get_storage_driver()
    assert driver.exists(key)

    engines.db.add(
        RetentionPolicy(
            organization_id=engines.org,
            work_item_retention_days=90,
            auto_purge_enabled=True,
        )
    )
    engines.db.execute(
        text("UPDATE work_items SET created_at = :old WHERE id = :id"),
        {"old": datetime.now(timezone.utc) - timedelta(days=120), "id": work_item_id},
    )
    engines.db.commit()

    factory = sessionmaker(bind=engines.db.get_bind())
    deleted = sweep_compliance._purge_work_items(
        factory, organization_id=engines.org, days=90, apply=True, batch_size=50
    )

    assert deleted == 1
    engines.refresh()
    assert engines.db.execute(select(WorkItem.id).where(WorkItem.id == work_item_id)).first() is None
    assert not driver.exists(key), "the purged document's file is still in storage"
    released = engines.db.execute(select(UploadedFile).where(UploadedFile.id == file_id)).scalar_one()
    assert released.deleted_at is not None, "the purged document's upload record is still live"

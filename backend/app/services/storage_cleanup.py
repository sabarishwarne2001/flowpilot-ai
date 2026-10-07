"""Deleting the stored bytes of files the database has released (F-151, F-152).

A document delete and a subject erasure mark `uploaded_files` rows deleted inside their
transaction; the objects in storage are deleted here, after that transaction commits, so a
rolled-back delete never loses a file. A file a supplier invoice keeps as its source document
is a financial record (the erasure's PRESERVED_FINANCIAL_TABLES): its bytes are kept.

Storage errors are logged, never raised: the rows are already committed, and the caller's
request has succeeded.
"""

from __future__ import annotations

import logging
from typing import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

logger = logging.getLogger("app.services.storage_cleanup")


def deletable_keys(db: Session, keys: Iterable[str]) -> list[str]:
    """`keys` without those of files a supplier invoice keeps as its source document."""
    from app.models.supplier_cogs import SupplierInvoice
    from app.models.uploaded_file import UploadedFile

    wanted = sorted({key for key in keys if key})
    if not wanted:
        return []
    kept = set(
        db.execute(
            select(UploadedFile.file_path)
            .join(SupplierInvoice, SupplierInvoice.raw_document_file_id == UploadedFile.id)
            .where(UploadedFile.file_path.in_(wanted))
        ).scalars()
    )
    return [key for key in wanted if key not in kept]


def delete_stored_objects(keys: Iterable[str]) -> int:
    """Delete each object; returns how many were deleted. Never raises."""
    from app.core.storage import get_storage_driver

    deleted = 0
    driver = None
    for key in sorted({key for key in keys if key}):
        try:
            driver = driver or get_storage_driver()
            if driver.delete(key):
                deleted += 1
        except Exception:  # noqa: BLE001 - see the module docstring
            logger.warning("storage.released_file_delete_failed", extra={"key": key}, exc_info=True)
    return deleted


__all__ = ["delete_stored_objects", "deletable_keys"]

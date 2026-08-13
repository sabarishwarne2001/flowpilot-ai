"""Storage driver factory (ARCH-07 §B.8)."""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Optional

from app.core.config import settings
from app.core.storage.base import (
    InvalidStorageKeyError,
    ObjectNotFoundError,
    StorageDriver,
    StorageError,
    sanitize_key,
)
from app.core.storage.local import LocalStorageDriver

__all__ = [
    "StorageDriver",
    "StorageError",
    "ObjectNotFoundError",
    "InvalidStorageKeyError",
    "LocalStorageDriver",
    "sanitize_key",
    "get_storage_driver",
    "reset_storage_driver",
]

_driver: Optional[StorageDriver] = None
_lock = threading.Lock()


def get_storage_driver() -> StorageDriver:
    global _driver
    if _driver is not None:
        return _driver

    with _lock:
        if _driver is not None:
            return _driver

        backend = settings.STORAGE_BACKEND
        if backend == "local":
            _driver = LocalStorageDriver(root=Path(settings.UPLOAD_DIR))
        else:
            raise StorageError(f"Unknown STORAGE_BACKEND: {backend!r}")

        return _driver


def reset_storage_driver() -> None:
    global _driver
    with _lock:
        _driver = None
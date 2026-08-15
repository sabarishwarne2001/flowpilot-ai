"""Storage driver factory (ARCH-07 §B.8, ARCH-08 §B.11)."""

from __future__ import annotations

import logging
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

logger = logging.getLogger(__name__)

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

        backend = settings.STORAGE_BACKEND.strip().lower()
        if backend == "local":
            _driver = LocalStorageDriver(root=Path(settings.UPLOAD_DIR))
        elif backend == "s3":
            from app.core.storage.s3 import S3StorageDriver
            _driver = S3StorageDriver(
                bucket=settings.S3_BUCKET or "flowpilot-uploads",
                region=settings.S3_REGION,
                endpoint_url=settings.S3_ENDPOINT_URL,
                prefix=settings.S3_PREFIX,
                sse=settings.S3_SERVER_SIDE_ENCRYPTION,
                max_pool_connections=settings.S3_MAX_POOL_CONNECTIONS,
            )
        else:
            raise StorageError(f"Unknown STORAGE_BACKEND: {backend!r}")

        return _driver


def reset_storage_driver() -> None:
    global _driver
    with _lock:
        _driver = None
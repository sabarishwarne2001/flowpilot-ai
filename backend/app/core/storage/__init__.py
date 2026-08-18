"""Storage driver factory (ARCH-07 §B.8, ARCH-08 §B.11, ARCH-10 Step 4)."""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Optional

from app.core.config import settings
from app.core.storage.base import (
    DEFAULT_CHUNK_SIZE,
    InvalidStorageKeyError,
    ObjectNotFoundError,
    StorageCapabilityError,
    StorageDriver,
    StorageError,
    StoredObject,
    sanitize_key,
)
from app.core.storage.keys import (
    ParsedKey,
    StorageNamespace,
    TenantKeyError,
    assert_key_belongs_to,
    parse_key,
    tenant_key,
    tenant_prefix,
)
from app.core.storage.local import LocalStorageDriver

logger = logging.getLogger(__name__)

__all__ = [
    "StorageDriver",
    "StorageError",
    "StorageCapabilityError",
    "ObjectNotFoundError",
    "InvalidStorageKeyError",
    "StoredObject",
    "LocalStorageDriver",
    "StorageNamespace",
    "ParsedKey",
    "TenantKeyError",
    "tenant_key",
    "tenant_prefix",
    "parse_key",
    "assert_key_belongs_to",
    "sanitize_key",
    "get_storage_driver",
    "reset_storage_driver",
    "DEFAULT_CHUNK_SIZE",
]

_driver: Optional[StorageDriver] = None
_lock = threading.Lock()

_S3_BACKENDS = {"s3", "r2", "minio"}


def _build_s3_driver(backend: str) -> StorageDriver:
    from app.core.storage.s3 import (
        MinIOStorageDriver,
        R2StorageDriver,
        S3StorageDriver,
    )

    if not settings.S3_BUCKET:
        raise StorageError(
            f"STORAGE_BACKEND={backend!r} requires S3_BUCKET to be set. "
            "Refusing to fall back to a default bucket name."
        )

    common = dict(
        bucket=settings.S3_BUCKET,
        region=settings.S3_REGION,
        endpoint_url=settings.S3_ENDPOINT_URL,
        prefix=settings.S3_PREFIX,
        max_pool_connections=settings.S3_MAX_POOL_CONNECTIONS,
        multipart_threshold=settings.S3_MULTIPART_THRESHOLD,
        multipart_chunksize=settings.S3_MULTIPART_CHUNKSIZE,
        max_concurrency=settings.S3_MAX_CONCURRENCY,
    )

    if backend == "r2":
        return R2StorageDriver(**common)
    if backend == "minio":
        return MinIOStorageDriver(**common)
    return S3StorageDriver(sse=settings.S3_SERVER_SIDE_ENCRYPTION, **common)


def get_storage_driver() -> StorageDriver:
    global _driver
    if _driver is not None:
        return _driver

    with _lock:
        if _driver is not None:
            return _driver

        backend = settings.STORAGE_BACKEND.strip().lower()

        if backend == "local":
            if settings.ENVIRONMENT.strip().lower() in {"production", "prod"}:
                raise StorageError(
                    "STORAGE_BACKEND='local' in a production environment. Set "
                    "STORAGE_BACKEND to s3, r2 or minio."
                )
            _driver = LocalStorageDriver(root=Path(settings.UPLOAD_DIR))
        elif backend in _S3_BACKENDS:
            _driver = _build_s3_driver(backend)
        else:
            raise StorageError(
                f"Unknown STORAGE_BACKEND: {backend!r}. "
                f"Expected one of: local, {', '.join(sorted(_S3_BACKENDS))}."
            )

        logger.info(
            "storage.driver_selected",
            extra={
                "backend": _driver.backend_name,
                "presigned": _driver.supports_presigned,
                "multipart": _driver.supports_multipart,
            },
        )
        return _driver


def reset_storage_driver() -> None:
    global _driver
    with _lock:
        _driver = None
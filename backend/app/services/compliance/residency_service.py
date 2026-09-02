"""ARCH-20 — regional storage routing.

`app.core.storage.get_storage_driver()` caches exactly one driver in a module
global, built from settings.S3_BUCKET. That singleton is correct for everything
written before residency existed and must not be mutated: a request that
flipped the process-wide bucket would reroute every other tenant's writes for
as long as the process lived.

So this module keeps its own cache, keyed by region, and leaves the global
alone. GLOBAL resolves to the existing driver — the same bytes in the same
place as before ARCH-20 — and a pinned region resolves to a driver built from
S3_REGIONAL_BUCKETS.

The refusal is the point. If a tenant is pinned to EU and no EU bucket is
configured, this raises. It does not fall back to the default bucket. Silently
writing EU personal data into a US bucket is the exact failure this phase
exists to prevent, and a fallback would make the residency column decorative.
"""

from __future__ import annotations

import logging
import threading
import uuid
from typing import Optional

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.storage import (
    StorageDriver,
    StorageError,
    StorageNamespace,
    get_storage_driver,
    tenant_key,
)
from app.models.compliance import (
    DATA_RESIDENCY_REGION_VALUES,
    PINNED_REGIONS,
    REGION_GLOBAL,
)
from app.models.organization import Organization

logger = logging.getLogger("app.services.compliance.residency")

__all__ = [
    "ResidencyError",
    "ResidencyNotConfiguredError",
    "UnknownRegionError",
    "driver_for_region",
    "driver_for_organization",
    "export_key",
    "known_regions",
    "region_for_organization",
    "regional_bucket_map",
    "reset_regional_drivers",
    "set_organization_region",
]


class ResidencyError(StorageError):
    """Base class for residency routing failures."""


class UnknownRegionError(ResidencyError):
    """A region outside the closed vocabulary was supplied."""


class ResidencyNotConfiguredError(ResidencyError):
    """A tenant is pinned to a region with no bucket provisioned for it."""


_regional_drivers: dict[str, StorageDriver] = {}
_lock = threading.Lock()


def known_regions() -> tuple[str, ...]:
    return DATA_RESIDENCY_REGION_VALUES


def regional_bucket_map() -> dict[str, str]:
    """The configured region -> bucket mapping, normalised and validated.

    Reads settings.S3_REGIONAL_BUCKETS. Unknown keys are dropped with a
    warning rather than raising: an operator typo in an env var should not
    take the whole process down at import time, but it must not silently
    become a routing rule either.
    """
    raw = getattr(settings, "S3_REGIONAL_BUCKETS", None) or {}
    mapping: dict[str, str] = {}
    for key, value in dict(raw).items():
        region = str(key).strip().upper()
        bucket = str(value).strip()
        if not bucket:
            continue
        if region not in DATA_RESIDENCY_REGION_VALUES:
            logger.warning(
                "residency.unknown_region_in_config",
                extra={"region": region},
            )
            continue
        mapping[region] = bucket
    return mapping


def _normalise(region: Optional[str]) -> str:
    value = (region or REGION_GLOBAL).strip().upper()
    if value not in DATA_RESIDENCY_REGION_VALUES:
        raise UnknownRegionError(
            f"Unknown residency region {region!r}. "
            f"Expected one of: {', '.join(DATA_RESIDENCY_REGION_VALUES)}."
        )
    return value


def _build_regional_driver(region: str, bucket: str) -> StorageDriver:
    backend = settings.STORAGE_BACKEND.strip().lower()
    if backend not in {"s3", "r2", "minio"}:
        raise ResidencyNotConfiguredError(
            f"Residency region {region} requires an object-storage backend. "
            f"STORAGE_BACKEND is {backend!r}, which has no concept of a region."
        )

    from app.core.storage.s3 import (
        MinIOStorageDriver,
        R2StorageDriver,
        S3StorageDriver,
    )

    common = dict(
        bucket=bucket,
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


def driver_for_region(region: Optional[str]) -> StorageDriver:
    """Resolve the storage driver that serves one residency region."""
    normalised = _normalise(region)

    if normalised == REGION_GLOBAL:
        return get_storage_driver()

    cached = _regional_drivers.get(normalised)
    if cached is not None:
        return cached

    with _lock:
        cached = _regional_drivers.get(normalised)
        if cached is not None:
            return cached

        buckets = regional_bucket_map()
        bucket = buckets.get(normalised)
        if not bucket:
            raise ResidencyNotConfiguredError(
                f"Organization is pinned to residency region {normalised}, but "
                f"S3_REGIONAL_BUCKETS has no bucket for it. Refusing to fall "
                f"back to the default bucket — that would place "
                f"{normalised}-resident data outside {normalised}."
            )

        driver = _build_regional_driver(normalised, bucket)
        _regional_drivers[normalised] = driver
        logger.info(
            "residency.driver_selected",
            extra={"region": normalised, "bucket": bucket},
        )
        return driver


def reset_regional_drivers() -> None:
    """Drop the regional driver cache. Test-support only."""
    global _regional_drivers
    with _lock:
        _regional_drivers = {}


def region_for_organization(organization: Organization) -> str:
    return _normalise(getattr(organization, "data_residency_region", None))


def driver_for_organization(organization: Organization) -> StorageDriver:
    return driver_for_region(region_for_organization(organization))


def export_key(organization_id: uuid.UUID, export_id: uuid.UUID) -> str:
    """The one legal key shape for a DPA archive."""
    return tenant_key(
        organization_id=organization_id,
        namespace=StorageNamespace.EXPORTS,
        file_id=export_id,
        suffix="zip",
    )


def set_organization_region(
    db: Session,
    *,
    organization: Organization,
    region: str,
    verify_backend: bool = True,
) -> str:
    """Repin a tenant and return the previous region.

    `verify_backend` resolves the driver before committing the change. Without
    it an operator could set EU on a deployment with no EU bucket and discover
    the mistake at the next upload, by which time the policy row claims a
    guarantee the platform cannot honour. Failing here is the cheaper failure.

    Note what this does NOT do: it does not migrate existing objects. Bytes
    written under the old region stay where they were written. Repinning is a
    forward-looking policy change, and the compliance UI says so.
    """
    normalised = _normalise(region)
    previous = region_for_organization(organization)

    if verify_backend and normalised in PINNED_REGIONS:
        driver_for_region(normalised)

    organization.data_residency_region = normalised
    db.add(organization)
    db.flush()
    return previous

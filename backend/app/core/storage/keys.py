"""ARCH-10 Step 4 — tenant-scoped storage keys.

The §8.2 decision is one bucket with per-tenant path prefixes:

    {organization_id}/documents/{file_id}
    {organization_id}/quarantine/{file_id}
    {organization_id}/avatars/{file_id}
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from enum import Enum as PyEnum
from typing import Optional

from app.core.storage.base import InvalidStorageKeyError, sanitize_key


class StorageNamespace(str, PyEnum):
    """Segment 1 of every tenant key."""

    DOCUMENTS = "documents"
    QUARANTINE = "quarantine"
    AVATARS = "avatars"
    LOGOS = "logos"
    EXPORTS = "exports"
    DERIVED = "derived"


@dataclass(frozen=True)
class ParsedKey:
    organization_id: uuid.UUID
    namespace: StorageNamespace
    file_id: str
    suffix: Optional[str]


class TenantKeyError(InvalidStorageKeyError):
    """A key was built or parsed outside the tenant grammar."""


def _coerce_org(organization_id: uuid.UUID | str) -> uuid.UUID:
    if isinstance(organization_id, uuid.UUID):
        return organization_id
    try:
        return uuid.UUID(str(organization_id))
    except (ValueError, AttributeError, TypeError) as exc:
        raise TenantKeyError(
            f"organization_id {organization_id!r} is not a UUID. A tenant key "
            "must never be built from an unvalidated identifier."
        ) from exc


def tenant_key(
    *,
    organization_id: uuid.UUID | str,
    namespace: StorageNamespace,
    file_id: uuid.UUID | str,
    suffix: Optional[str] = None,
) -> str:
    """Build the one legal key shape for a tenant-owned object."""
    org = _coerce_org(organization_id)

    if not isinstance(namespace, StorageNamespace):
        raise TenantKeyError(f"namespace must be a StorageNamespace, got {namespace!r}")

    file_part = str(file_id)
    if suffix:
        clean = suffix.lstrip(".").lower()
        if not clean.isalnum() or len(clean) > 8:
            raise TenantKeyError(f"Illegal key suffix {suffix!r}")
        file_part = f"{file_part}.{clean}"

    return sanitize_key(f"{org}/{namespace.value}/{file_part}")


def tenant_prefix(
    *,
    organization_id: uuid.UUID | str,
    namespace: Optional[StorageNamespace] = None,
) -> str:
    """The listing prefix for one tenant, optionally narrowed to a namespace."""
    org = _coerce_org(organization_id)
    if namespace is None:
        return f"{org}/"
    return f"{org}/{namespace.value}/"


def parse_key(key: str) -> ParsedKey:
    """Decompose a stored key. Raises if it is not in the tenant grammar."""
    safe = sanitize_key(key)
    parts = safe.split("/")
    if len(parts) != 3:
        raise TenantKeyError(
            f"Key {key!r} is not a tenant key: expected "
            "'{organization_id}/{namespace}/{file_id}'"
        )
    org_part, namespace_part, file_part = parts

    try:
        organization_id = uuid.UUID(org_part)
    except ValueError as exc:
        raise TenantKeyError(
            f"Key {key!r} has a non-UUID tenant segment {org_part!r}"
        ) from exc

    try:
        namespace = StorageNamespace(namespace_part)
    except ValueError as exc:
        raise TenantKeyError(
            f"Key {key!r} has an unknown namespace {namespace_part!r}"
        ) from exc

    file_id, _, suffix = file_part.partition(".")
    return ParsedKey(
        organization_id=organization_id,
        namespace=namespace,
        file_id=file_id,
        suffix=suffix or None,
    )


def assert_key_belongs_to(key: str, organization_id: uuid.UUID | str) -> str:
    """Verify a stored key is within the caller's tenant, or raise."""
    parsed = parse_key(key)
    expected = _coerce_org(organization_id)
    if parsed.organization_id != expected:
        raise TenantKeyError(
            f"Storage key {key!r} belongs to organization "
            f"{parsed.organization_id}, not {expected}."
        )
    return key
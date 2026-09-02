"""Database operations for API Keys (ARCH-08 §B.1, §9.6, §10.1)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Optional, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.api_key import ApiKey


def create_api_key(
    db: Session,
    *,
    organization_id: uuid.UUID,
    user_id: Optional[uuid.UUID],
    name: str,
    secret_hash: str,
    scopes: list[str],
    expires_at: Optional[datetime] = None,
) -> ApiKey:
    key = ApiKey(
        organization_id=organization_id,
        user_id=user_id,
        name=name,
        secret_hash=secret_hash,
        scopes=scopes,
        expires_at=expires_at,
    )
    db.add(key)
    db.flush()
    return key


def get_api_key_by_id(
    db: Session, *, organization_id: uuid.UUID, key_id: uuid.UUID
) -> Optional[ApiKey]:
    stmt = select(ApiKey).where(
        ApiKey.organization_id == organization_id,
        ApiKey.id == key_id,
    )
    return db.execute(stmt).scalar_one_or_none()


def list_api_keys_for_organization(
    db: Session, *, organization_id: uuid.UUID, include_deactivated: bool = False
) -> list[ApiKey]:
    stmt = select(ApiKey).where(ApiKey.organization_id == organization_id)
    if not include_deactivated:
        stmt = stmt.where(ApiKey.deactivated_at.is_(None))
    stmt = stmt.order_by(ApiKey.created_at.desc())
    return list(db.execute(stmt).scalars().all())


def deactivate_api_key(
    db: Session, *, key: ApiKey, reason: str = "MANUAL"
) -> ApiKey:
    key.deactivated_at = datetime.now(UTC)
    key.deactivated_reason = reason
    db.add(key)
    db.flush()
    return key


def revoke_keys_for_issuer(
    db: Session, *, organization_id: uuid.UUID, user_id: uuid.UUID, reason: str = "OFFBOARDED"
) -> list[ApiKey]:
    stmt = select(ApiKey).where(
        ApiKey.organization_id == organization_id,
        ApiKey.user_id == user_id,
        ApiKey.deactivated_at.is_(None),
    )
    keys = list(db.execute(stmt).scalars().all())
    now = datetime.now(UTC)
    for key in keys:
        key.deactivated_at = now
        key.deactivated_reason = reason
        db.add(key)
    db.flush()
    return keys

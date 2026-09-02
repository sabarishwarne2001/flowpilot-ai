"""Business orchestration for API Keys (ARCH-08 §B.1, §B.2, §B.4, §B.12, §10.1)."""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Optional

from sqlalchemy.orm import Session

from app.core.api_key_secret import (
    generate_secret,
    hash_secret,
    mint_api_key_token,
    parse_api_key_token,
    verify_secret,
)
from app.core.exceptions import (
    OrganizationPermissionDeniedError,
    WorkspacePermissionDeniedError,
)
from app.crud import api_key as api_key_crud
from app.crud import organization_members as organization_members_crud
from app.crud.membership_filters import ACTIVE_ONLY
from app.models.api_key import ApiKey
from app.models.audit_log import AuditAction, AuditResourceType
from app.models.organization import OrganizationMember, OrganizationRole
from app.services import audit_service

logger = logging.getLogger("app.services.api_key_service")

ROTATION_OVERLAP_DAYS = 7


def issue_api_key(
    db: Session,
    *,
    organization_id: uuid.UUID,
    actor: OrganizationMember,
    name: str,
    scopes: list[str],
    expires_at: Optional[datetime] = None,
) -> tuple[ApiKey, str]:
    if actor.role not in (OrganizationRole.OWNER, OrganizationRole.ADMIN):
        raise OrganizationPermissionDeniedError("Only organization admins can issue API keys.")

    secret = generate_secret()
    sec_hash = hash_secret(secret)

    key = api_key_crud.create_api_key(
        db,
        organization_id=organization_id,
        user_id=actor.user_id,
        name=name,
        secret_hash=sec_hash,
        scopes=scopes,
        expires_at=expires_at,
    )

    token = mint_api_key_token(key.id, secret)

    audit_service.record(
        db,
        organization_id=organization_id,
        actor_id=actor.user_id,
        resource_type=AuditResourceType.API_KEY,
        resource_id=key.id,
        action=AuditAction.CREATED,
        details={
            **audit_service.actor_snapshot(actor.user),
            "key_name": key.name,
            "scopes": key.scopes,
        },
    )

    return key, token


def authenticate_api_key_token(
    db: Session, *, token: str
) -> Optional[tuple[ApiKey, OrganizationMember]]:
    parsed = parse_api_key_token(token)
    if parsed is None:
        return None

    key = db.get(ApiKey, parsed.key_id)
    if key is None or key.deactivated_at is not None:
        return None

    now = datetime.now(UTC)
    if key.expires_at is not None and key.expires_at <= now:
        return None

    valid_secret = verify_secret(parsed.secret, key.secret_hash)

    if not valid_secret and key.previous_secret_hash is not None:
        if key.previous_secret_expires_at is not None and key.previous_secret_expires_at > now:
            valid_secret = verify_secret(parsed.secret, key.previous_secret_hash)

    if not valid_secret:
        return None

    if key.user_id is None:
        return None

    membership = organization_members_crud.get_organization_member(
        db,
        organization_id=key.organization_id,
        user_id=key.user_id,
        statuses=ACTIVE_ONLY,
    )
    if membership is None:
        return None

    # Coarsen last_used_at update to 5 minutes (300 seconds); no commit on auth path (F7)
    if key.last_used_at is None or (now - key.last_used_at).total_seconds() >= 300:
        key.last_used_at = now
        db.add(key)

    return key, membership


def rotate_api_key(
    db: Session,
    *,
    organization_id: uuid.UUID,
    key_id: uuid.UUID,
    actor: OrganizationMember,
    force: bool = False,
) -> tuple[ApiKey, str]:
    if actor.role not in (OrganizationRole.OWNER, OrganizationRole.ADMIN):
        raise OrganizationPermissionDeniedError("Only organization admins can rotate API keys.")

    key = api_key_crud.get_api_key_by_id(db, organization_id=organization_id, key_id=key_id)
    if key is None or key.deactivated_at is not None:
        raise WorkspacePermissionDeniedError("API key not found.")

    now = datetime.now(UTC)
    if not force and key.previous_secret_expires_at is not None and key.previous_secret_expires_at > now:
        raise WorkspacePermissionDeniedError("Rotation overlap is currently active. Use force=True to override.")

    new_secret = generate_secret()
    new_hash = hash_secret(new_secret)

    key.previous_secret_hash = key.secret_hash
    key.previous_secret_expires_at = now + timedelta(days=ROTATION_OVERLAP_DAYS)
    key.secret_hash = new_hash

    db.add(key)

    audit_service.record(
        db,
        organization_id=organization_id,
        actor_id=actor.user_id,
        resource_type=AuditResourceType.API_KEY,
        resource_id=key.id,
        action=AuditAction.ROTATED,
        details={
            "key_name": key.name,
            "overlap_expires_at": key.previous_secret_expires_at.isoformat(),
        },
    )

    token = mint_api_key_token(key.id, new_secret)
    return key, token

"""
Audit trail write service for FlowPilot AI (ARCH-07 §B.2 Option C).

Provides two entry points:
- record(db, ...): flushes into the caller's active transaction (caller commits).
- record_independently([db], ...): uses an independent session or bound connection for denials.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, Mapping, Optional, Union

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.models.audit_log import AuditAction, AuditLog, AuditResourceType

logger = logging.getLogger("app.services.audit")

_IP_ADDRESS_MAX = 45
_USER_AGENT_MAX = 512

_REDACTED = "[REDACTED]"
_SENSITIVE_KEY_FRAGMENTS = (
    "password",
    "passwd",
    "secret",
    "token",
    "api_key",
    "apikey",
    "authorization",
    "credential",
    "private_key",
    "encrypted_",
    "ciphertext",
)

_MAX_DETAIL_DEPTH = 6
_MAX_DETAIL_ITEMS = 100

ResourceTypeLike = Union[AuditResourceType, str]
ActionLike = Union[AuditAction, str]


def actor_snapshot(user: Any) -> dict[str, Any]:
    """
    Denormalise the acting user's identity into details payload.
    Used across converted service call sites.
    """
    if user is None:
        return {"actor": None}
    return {
        "actor_email": getattr(user, "email", None),
        "actor_display_name": getattr(user, "display_name", None),
    }


def _coerce_resource_type(value: ResourceTypeLike) -> AuditResourceType:
    if isinstance(value, AuditResourceType):
        return value
    try:
        return AuditResourceType(value)
    except ValueError as exc:
        raise ValueError(
            f"Unknown audit resource_type {value!r}. Valid values: "
            f"{[member.value for member in AuditResourceType]}"
        ) from exc


def _coerce_action(value: ActionLike) -> AuditAction:
    if isinstance(value, AuditAction):
        return value
    try:
        return AuditAction(value)
    except ValueError as exc:
        raise ValueError(
            f"Unknown audit action {value!r}. Valid values: "
            f"{[member.value for member in AuditAction]}"
        ) from exc


def _is_sensitive_key(key: str) -> bool:
    lowered = key.lower()
    return any(fragment in lowered for fragment in _SENSITIVE_KEY_FRAGMENTS)


def _sanitize(value: Any, depth: int = 0) -> Any:
    if depth >= _MAX_DETAIL_DEPTH:
        return "[TRUNCATED: max depth]"

    if isinstance(value, Mapping):
        cleaned: dict[str, Any] = {}
        for index, (key, item) in enumerate(value.items()):
            if index >= _MAX_DETAIL_ITEMS:
                cleaned["__truncated__"] = f"{len(value) - _MAX_DETAIL_ITEMS} more keys"
                break
            key_str = str(key)
            cleaned[key_str] = (
                _REDACTED if _is_sensitive_key(key_str) else _sanitize(item, depth + 1)
            )
        return cleaned

    if isinstance(value, (list, tuple, set)):
        items = list(value)[:_MAX_DETAIL_ITEMS]
        return [_sanitize(item, depth + 1) for item in items]

    if isinstance(value, uuid.UUID):
        return str(value)

    if value is None or isinstance(value, (str, int, float, bool)):
        return value

    if hasattr(value, "isoformat"):
        return value.isoformat()
    if hasattr(value, "value") and hasattr(value, "name"):
        return value.value
    return str(value)


def _sanitize_details(details: Optional[Mapping[str, Any]]) -> Optional[dict[str, Any]]:
    if details is None:
        return None
    sanitized = _sanitize(details)
    return sanitized if isinstance(sanitized, dict) else {"value": sanitized}


def _truncate(value: Optional[str], limit: int) -> Optional[str]:
    if value is None:
        return None
    value = value.strip()
    if not value:
        return None
    return value[:limit]


def _build(
    *,
    organization_id: uuid.UUID,
    workspace_id: Optional[uuid.UUID],
    actor_id: Optional[uuid.UUID],
    resource_type: ResourceTypeLike,
    resource_id: Optional[uuid.UUID],
    action: ActionLike,
    details: Optional[Mapping[str, Any]],
    ip_address: Optional[str],
    user_agent: Optional[str],
) -> AuditLog:
    if organization_id is None:
        raise ValueError("audit_service: organization_id is required")

    return AuditLog(
        organization_id=organization_id,
        workspace_id=workspace_id,
        actor_id=actor_id,
        resource_type=_coerce_resource_type(resource_type),
        resource_id=resource_id,
        action=_coerce_action(action),
        details=_sanitize_details(details),
        ip_address=_truncate(ip_address, _IP_ADDRESS_MAX),
        user_agent=_truncate(user_agent, _USER_AGENT_MAX),
    )


def record(
    db: Session,
    *,
    organization_id: uuid.UUID,
    workspace_id: Optional[uuid.UUID] = None,
    actor_id: Optional[uuid.UUID] = None,
    resource_type: ResourceTypeLike,
    resource_id: Optional[uuid.UUID] = None,
    action: ActionLike,
    details: Optional[Mapping[str, Any]] = None,
    ip_address: Optional[str] = None,
    user_agent: Optional[str] = None,
) -> AuditLog:
    """Record an audit event in the caller's transaction (caller commits)."""
    entry = _build(
        organization_id=organization_id,
        workspace_id=workspace_id,
        actor_id=actor_id,
        resource_type=resource_type,
        resource_id=resource_id,
        action=action,
        details=details,
        ip_address=ip_address,
        user_agent=user_agent,
    )
    db.add(entry)
    db.flush([entry])
    return entry


def record_independently(
    db: Optional[Session] = None,
    *,
    organization_id: uuid.UUID,
    workspace_id: Optional[uuid.UUID] = None,
    actor_id: Optional[uuid.UUID] = None,
    resource_type: ResourceTypeLike,
    resource_id: Optional[uuid.UUID] = None,
    action: ActionLike,
    details: Optional[Mapping[str, Any]] = None,
    ip_address: Optional[str] = None,
    user_agent: Optional[str] = None,
) -> Optional[uuid.UUID]:
    """Record an audit event in an independent session (or bound session if db provided).

    For events that must survive the rollback of the request that produced
    them — authorization denials above all.
    """
    try:
        entry = _build(
            organization_id=organization_id,
            workspace_id=workspace_id,
            actor_id=actor_id,
            resource_type=resource_type,
            resource_id=resource_id,
            action=action,
            details=details,
            ip_address=ip_address,
            user_agent=user_agent,
        )
    except ValueError:
        logger.exception("audit_service: refusing to record malformed event")
        return None

    if db is not None:
        session = Session(bind=db.get_bind())
        try:
            session.add(entry)
            session.flush([entry])
            session.commit()
            return entry.id
        except SQLAlchemyError:
            logger.exception("audit_service: independent audit write (bound) FAILED")
            session.rollback()
            return None
        finally:
            session.close()

    session: Optional[Session] = None
    try:
        session = SessionLocal()
        session.add(entry)
        session.commit()
        return entry.id
    except SQLAlchemyError:
        logger.exception("audit_service: independent audit write FAILED")
        if session is not None:
            try:
                session.rollback()
            except SQLAlchemyError:
                pass
        return None
    finally:
        if session is not None:
            session.close()


def context_from_request(request: Any) -> dict[str, Optional[str]]:
    if request is None:
        return {"ip_address": None, "user_agent": None}

    headers = getattr(request, "headers", {}) or {}
    forwarded = headers.get("x-forwarded-for")
    if forwarded:
        ip_address: Optional[str] = forwarded.split(",")[0].strip()
    else:
        client = getattr(request, "client", None)
        ip_address = getattr(client, "host", None) if client else None

    return {
        "ip_address": _truncate(ip_address, _IP_ADDRESS_MAX),
        "user_agent": _truncate(headers.get("user-agent"), _USER_AGENT_MAX),
    }
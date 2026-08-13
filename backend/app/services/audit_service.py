"""Audit trail write service (ARCH-07 §B.2 Option C).

Two entry points with deliberately different durability semantics:

``record(db, ...)``
    Writes into the caller's transaction and ``flush()``es without
    committing. The audit row lives or dies with the change it describes.
    This is correct for the overwhelming majority of events: an ownership
    transfer that commits without its audit row is an untraceable privilege
    change, and an audit row that survives a rolled-back change is a *false*
    record — worse than a missing one, because it will be believed.

``record_independently(...)``
    Opens its own session and commits on its own. Reserved for events with no
    successful transaction to ride on — principally authorization denials,
    which are exactly what an auditor most wants and which by definition end
    in a rollback. Do NOT reach for this because it seems safer. It is not; it
    is a different tradeoff, and using it by default reintroduces the false
    -record problem this design exists to avoid.

Neither function raises on failure to write in the independent path. A
denied request must still return 403 to the caller even if the audit sink is
unavailable; the failure is logged at ERROR for alerting.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, Mapping, Optional, Union

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.models.audit_log import AuditAction, AuditLog, AuditResourceType

logger = logging.getLogger(__name__)

# Column widths from the model. Enforced here so that a long user-agent
# string produces a truncated audit row rather than a DataError that rolls
# back the business transaction it was riding on.
_IP_ADDRESS_MAX = 45
_USER_AGENT_MAX = 512

# Defence in depth. Callers should not put secrets in `details`; this ensures
# that a careless `details=payload.model_dump()` does not persist one.
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

# Bounded recursion: `details` is operator-facing metadata, not a document
# store. A deeply nested payload is a caller bug; truncate rather than
# stack-overflow on a cyclic structure.
_MAX_DETAIL_DEPTH = 6
_MAX_DETAIL_ITEMS = 100

ResourceTypeLike = Union[AuditResourceType, str]
ActionLike = Union[AuditAction, str]


# ---------------------------------------------------------------------------
# Normalisation helpers
# ---------------------------------------------------------------------------


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
    """Recursively redact sensitive keys and coerce to JSON-serialisable types."""
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

    # datetime, Decimal, Enum, ORM objects, anything else.
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if hasattr(value, "value") and hasattr(value, "name"):  # Enum-like
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
        # §B.4: NOT NULL from row zero. Fail here with a useful message rather
        # than at flush time with an IntegrityError naming only the column.
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


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


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
    """Record an audit event in the caller's transaction. Default path.

    Flushes so that ``entry.id`` is populated and any constraint violation
    surfaces at the call site rather than at an unrelated commit. **Does not
    commit** — the caller owns the transaction boundary, and the audit row
    must share the fate of the change it describes.

    Raises whatever the flush raises. That is intentional: if the audit row
    cannot be written, the change it describes should not commit either.
    """
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
    """Record an audit event in its own session and commit it. Escape hatch.

    For events that must survive the rollback of the request that produced
    them — authorization denials above all. Opens a fresh session (therefore a
    fresh connection and a separate transaction), writes one row, commits.

    Takes no ``Session`` argument by design: accepting one would invite a
    caller to pass the request session, whose rollback is precisely what this
    function exists to escape.

    Returns the new row's id, or ``None`` if the write failed. Never raises —
    a 403 must still reach the client when the audit sink is down. Failures
    are logged at ERROR and should be alerted on: a silent gap in the denial
    record is a security-relevant outage, not a nuisance.
    """
    session: Optional[Session] = None
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

    try:
        session = SessionLocal()
        session.add(entry)
        session.commit()
        return entry.id
    except SQLAlchemyError:
        logger.exception(
            "audit_service: independent audit write FAILED "
            "(org=%s resource=%s/%s action=%s actor=%s)",
            organization_id,
            resource_type,
            resource_id,
            action,
            actor_id,
        )
        if session is not None:
            try:
                session.rollback()
            except SQLAlchemyError:
                logger.exception("audit_service: rollback of audit session failed")
        return None
    finally:
        if session is not None:
            session.close()


# ---------------------------------------------------------------------------
# Request-context extraction
# ---------------------------------------------------------------------------


def context_from_request(request: Any) -> dict[str, Optional[str]]:
    """Extract ``ip_address`` and ``user_agent`` from a Starlette Request.

    Typed ``Any`` and imported nowhere so that ``audit_service`` stays free of
    a FastAPI dependency and can be called from scripts and sweepers.

    ``X-Forwarded-For`` is honoured only because the deployment sits behind a
    reverse proxy that sets it. If that ever stops being true, this becomes a
    client-controlled field and must be dropped — a spoofable IP in an audit
    log is worse than no IP, because it will be treated as evidence.
    """
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
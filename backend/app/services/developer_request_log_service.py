"""Developer request log: the per-request view of the public API gateway.

The daily rollup (`api_key_usage_daily`) answers "how much and how fast". It
cannot answer the question a developer debugging an integration asks first:
"which call failed, when, and with what status?". Every gateway request is
already written to the immutable `usage_events` ledger by
`public_api_service.meter_request` (route, method, status, latency, throttle
flag), so this is a read path over data that exists. No new table, no new
write on the hot path.

The query rides `ix_usage_events_org_type_occurred_at` and pages with a
keyset cursor on (occurred_at, seq), so page N costs the same as page 1.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from app.models.api_key import ApiKey
from app.models.usage_event import UsageEvent
from app.services.public_api_service import PUBLIC_API_EVENT_TYPE

OUTCOMES: tuple[str, ...] = ("all", "success", "error", "throttled")
DEFAULT_WINDOW_DAYS = 7
MAX_WINDOW_DAYS = 30
DEFAULT_LIMIT = 50
MAX_LIMIT = 100


class InvalidCursorError(ValueError):
    """The cursor was not one this service issued."""


def encode_cursor(occurred_at: datetime, seq: int) -> str:
    return f"{occurred_at.isoformat()}_{seq}"


def decode_cursor(cursor: str) -> tuple[datetime, int]:
    try:
        stamp, seq = cursor.rsplit("_", 1)
        parsed = datetime.fromisoformat(stamp)
        if parsed.tzinfo is None:
            raise ValueError("naive timestamp")
        return parsed, int(seq)
    except (ValueError, TypeError) as exc:
        raise InvalidCursorError("Invalid cursor.") from exc


def _outcome_clause(outcome: str):
    status = UsageEvent.details["status_code"].as_integer()
    throttled = UsageEvent.details["throttled"].as_boolean()
    not_throttled = or_(throttled.is_(None), throttled.is_(False))
    if outcome == "success":
        return and_(status < 400, not_throttled)
    if outcome == "error":
        return and_(status >= 400, not_throttled)
    if outcome == "throttled":
        return throttled.is_(True)
    return None


def _as_float(value) -> Optional[float]:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _as_int(value) -> Optional[int]:
    return int(value) if isinstance(value, int) and not isinstance(value, bool) else None


def list_requests(
    db: Session,
    *,
    organization_id: uuid.UUID,
    api_key_id: Optional[uuid.UUID] = None,
    outcome: str = "all",
    days: int = DEFAULT_WINDOW_DAYS,
    limit: int = DEFAULT_LIMIT,
    cursor: Optional[str] = None,
    now: Optional[datetime] = None,
) -> dict:
    if outcome not in OUTCOMES:
        raise ValueError(f"outcome must be one of {OUTCOMES}")
    days = max(1, min(int(days), MAX_WINDOW_DAYS))
    limit = max(1, min(int(limit), MAX_LIMIT))
    since = (now or datetime.now(timezone.utc)) - timedelta(days=days)

    stmt = (
        select(UsageEvent, ApiKey.name)
        .outerjoin(
            ApiKey,
            and_(
                ApiKey.id == UsageEvent.api_key_id,
                ApiKey.organization_id == UsageEvent.organization_id,
            ),
        )
        .where(
            UsageEvent.organization_id == organization_id,
            UsageEvent.event_type == PUBLIC_API_EVENT_TYPE,
            UsageEvent.occurred_at >= since,
        )
    )
    if api_key_id is not None:
        stmt = stmt.where(UsageEvent.api_key_id == api_key_id)
    clause = _outcome_clause(outcome)
    if clause is not None:
        stmt = stmt.where(clause)
    if cursor:
        at, seq = decode_cursor(cursor)
        stmt = stmt.where(
            or_(
                UsageEvent.occurred_at < at,
                and_(UsageEvent.occurred_at == at, UsageEvent.seq < seq),
            )
        )
    stmt = stmt.order_by(
        UsageEvent.occurred_at.desc(), UsageEvent.seq.desc()
    ).limit(limit + 1)

    rows = db.execute(stmt).all()
    page, more = rows[:limit], len(rows) > limit

    items = []
    for event, key_name in page:
        details = event.details or {}
        throttled = details.get("throttled") is True
        items.append(
            {
                "id": str(event.id),
                "occurred_at": event.occurred_at,
                "api_key_id": str(event.api_key_id) if event.api_key_id else None,
                "api_key_name": key_name,
                "method": details.get("method"),
                "route": details.get("route"),
                "status_code": _as_int(details.get("status_code")),
                "latency_ms": None if throttled else _as_float(details.get("latency_ms")),
                "throttled": throttled,
                "workspace_id": str(event.workspace_id) if event.workspace_id else None,
            }
        )

    next_cursor = None
    if more and page:
        last = page[-1][0]
        next_cursor = encode_cursor(last.occurred_at, last.seq)

    return {
        "window_days": days,
        "outcome": outcome,
        "items": items,
        "next_cursor": next_cursor,
    }

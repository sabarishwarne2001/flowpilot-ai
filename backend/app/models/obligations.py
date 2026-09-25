"""ARCH46-S1:models — obligations, their events, holiday calendars and calendar feed tokens.

The schema (every CHECK, the composite FKs with column-list SET NULL, the
partial UNIQUE indexes and the two triggers) lives in
alembic/versions/arch46_step1_obligations.py; these mappings are the ORM view,
with column types matching it exactly (verify_arch46 D2 compares with
alembic's autogenerate).
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Optional

from sqlalchemy import CHAR, BigInteger, Boolean, Date, DateTime, ForeignKey, Integer, Numeric, SmallInteger, String, Text
from sqlalchemy import text as sa_text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


def _uuid(*args: Any, **kw: Any) -> Any:
    return mapped_column(PgUUID(as_uuid=True), *args, **kw)


def _ts(nullable: bool = False) -> Any:
    if nullable:
        return mapped_column(DateTime(timezone=True), nullable=True)
    return mapped_column(DateTime(timezone=True), nullable=False, server_default=sa_text("now()"))


def _user_fk() -> Any:
    return _uuid(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)


class HolidayCalendar(Base):
    __tablename__ = "holiday_calendars"

    id: Mapped[uuid.UUID] = _uuid(primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = _uuid(nullable=False)
    workspace_id: Mapped[uuid.UUID] = _uuid(nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    source: Mapped[str] = mapped_column(String(10), nullable=False)
    template_code: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    region: Mapped[Optional[str]] = mapped_column(String(8), nullable=True)
    weekend_days: Mapped[list[int]] = mapped_column(ARRAY(SmallInteger), nullable=False,
                                                    server_default=sa_text("'{6,7}'"))
    holidays: Mapped[list[date]] = mapped_column(ARRAY(Date), nullable=False, server_default=sa_text("'{}'"))
    holiday_names: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, server_default=sa_text("'{}'"))
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=sa_text("false"))
    revision: Mapped[int] = mapped_column(Integer, nullable=False, server_default=sa_text("1"))
    created_by_user_id: Mapped[Optional[uuid.UUID]] = _user_fk()
    created_at: Mapped[datetime] = _ts()
    updated_at: Mapped[datetime] = _ts()


class Obligation(Base):
    __tablename__ = "obligations"

    id: Mapped[uuid.UUID] = _uuid(primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = _uuid(nullable=False)
    workspace_id: Mapped[uuid.UUID] = _uuid(nullable=False)
    work_item_id: Mapped[Optional[uuid.UUID]] = _uuid(nullable=True)
    entity_id: Mapped[Optional[uuid.UUID]] = _uuid(nullable=True)
    anchor_obligation_id: Mapped[Optional[uuid.UUID]] = _uuid(nullable=True)
    calendar_id: Mapped[Optional[uuid.UUID]] = _uuid(nullable=True)
    owner_user_id: Mapped[Optional[uuid.UUID]] = _uuid(nullable=True)
    kind: Mapped[str] = mapped_column(String(12), nullable=False)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    origin: Mapped[str] = mapped_column(String(10), nullable=False)
    review: Mapped[str] = mapped_column(String(10), nullable=False)
    state: Mapped[str] = mapped_column(String(10), nullable=False, server_default=sa_text("'OPEN'"))
    due_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    due_rule: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    recurrence: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)
    series_start: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    occurrence: Mapped[int] = mapped_column(Integer, nullable=False, server_default=sa_text("1"))
    completed_occurrences: Mapped[int] = mapped_column(Integer, nullable=False, server_default=sa_text("0"))
    business_day_rule: Mapped[str] = mapped_column(String(20), nullable=False, server_default=sa_text("'NONE'"))
    lead_days: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    amount: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 2), nullable=True)
    currency: Mapped[Optional[str]] = mapped_column(CHAR(3), nullable=True)
    counterparty_name: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)
    confidence: Mapped[Decimal] = mapped_column(Numeric(5, 4), nullable=False, server_default=sa_text("1"))
    reasons: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    evidence: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    quote: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    clause_number: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    derivation: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    source_key: Mapped[Optional[str]] = mapped_column(CHAR(64), nullable=True)
    engine_version: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    detail: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, server_default=sa_text("1"))
    created_by_user_id: Mapped[Optional[uuid.UUID]] = _user_fk()
    created_at: Mapped[datetime] = _ts()
    updated_at: Mapped[datetime] = _ts()
    state_changed_at: Mapped[datetime] = _ts()
    done_at: Mapped[Optional[datetime]] = _ts(nullable=True)
    done_by_user_id: Mapped[Optional[uuid.UUID]] = _user_fk()
    waived_at: Mapped[Optional[datetime]] = _ts(nullable=True)
    waived_by_user_id: Mapped[Optional[uuid.UUID]] = _user_fk()
    waiver_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    last_completed_at: Mapped[Optional[datetime]] = _ts(nullable=True)
    reviewed_at: Mapped[Optional[datetime]] = _ts(nullable=True)
    reviewed_by_user_id: Mapped[Optional[uuid.UUID]] = _user_fk()
    superseded_at: Mapped[Optional[datetime]] = _ts(nullable=True)


class ObligationEvent(Base):
    __tablename__ = "obligation_events"

    id: Mapped[uuid.UUID] = _uuid(primary_key=True, default=uuid.uuid4)
    obligation_id: Mapped[uuid.UUID] = _uuid(nullable=False)
    workspace_id: Mapped[uuid.UUID] = _uuid(nullable=False)
    kind: Mapped[str] = mapped_column(String(12), nullable=False)
    from_state: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    to_state: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    due_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    occurrence: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    emitted: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=sa_text("false"))
    outbox_event_id: Mapped[Optional[uuid.UUID]] = _uuid(nullable=True)
    actor_user_id: Mapped[Optional[uuid.UUID]] = _user_fk()
    detail: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = _ts()


class CalendarFeedToken(Base):
    __tablename__ = "calendar_feed_tokens"

    id: Mapped[uuid.UUID] = _uuid(primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = _uuid(nullable=False)
    workspace_id: Mapped[uuid.UUID] = _uuid(nullable=False)
    user_id: Mapped[uuid.UUID] = _uuid(nullable=False)
    label: Mapped[str] = mapped_column(String(100), nullable=False)
    scope: Mapped[str] = mapped_column(String(8), nullable=False)
    include_closed: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=sa_text("true"))
    token_hash: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    created_at: Mapped[datetime] = _ts()
    expires_at: Mapped[Optional[datetime]] = _ts(nullable=True)
    last_used_at: Mapped[Optional[datetime]] = _ts(nullable=True)
    use_count: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=sa_text("0"))
    revoked_at: Mapped[Optional[datetime]] = _ts(nullable=True)
    revoked_by_user_id: Mapped[Optional[uuid.UUID]] = _user_fk()


__all__ = ["CalendarFeedToken", "HolidayCalendar", "Obligation", "ObligationEvent"]

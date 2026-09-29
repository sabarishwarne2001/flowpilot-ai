"""ARCH50-S1:models — the sovereign edition: egress lockdown, refusals, the offline licence, DR evidence.

The schema (every CHECK, the vocabulary, the expression UNIQUE on refusal buckets, the one-current-licence
partial UNIQUE) lives in alembic/versions/arch50_step1_sovereign_revops.py; these mappings are the ORM view
with column types matching it exactly (verify_arch50 D2 compares).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any, Optional

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Integer, Numeric, String
from sqlalchemy import text as sa_text
from sqlalchemy.dialects.postgresql import JSONB, UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


def _uuid(*args: Any, **kw: Any) -> Any:
    return mapped_column(PgUUID(as_uuid=True), *args, **kw)


def _ts(nullable: bool = False, server_now: bool = False) -> Any:
    if nullable:
        return mapped_column(DateTime(timezone=True), nullable=True)
    if server_now:
        return mapped_column(DateTime(timezone=True), nullable=False, server_default=sa_text("now()"))
    return mapped_column(DateTime(timezone=True), nullable=False)


def _user_fk() -> Any:
    return _uuid(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)


class EgressPolicy(Base):
    """One row per organization that ever touched its lockdown. No row = no lockdown."""

    __tablename__ = "egress_policies"

    organization_id: Mapped[uuid.UUID] = _uuid(ForeignKey("organizations.id", ondelete="CASCADE"),
                                               primary_key=True)
    lockdown_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=sa_text("false"))
    updated_by_user_id: Mapped[Optional[uuid.UUID]] = _user_fk()
    created_at: Mapped[datetime] = _ts(server_now=True)
    updated_at: Mapped[datetime] = _ts(server_now=True)


class EgressAllowRule(Base):
    __tablename__ = "egress_allow_rules"

    id: Mapped[uuid.UUID] = _uuid(primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = _uuid(ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False)
    channel: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    host_pattern: Mapped[str] = mapped_column(String(253), nullable=False)
    port: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    note: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    created_by_user_id: Mapped[Optional[uuid.UUID]] = _user_fk()
    created_at: Mapped[datetime] = _ts(server_now=True)


class EgressRefusal(Base):
    """A refused connection, bucketed per hour with a count (a retry storm is one row, not a million)."""

    __tablename__ = "egress_refusals"

    id: Mapped[uuid.UUID] = _uuid(primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[Optional[uuid.UUID]] = _uuid(ForeignKey("organizations.id", ondelete="CASCADE"),
                                                         nullable=True)
    channel: Mapped[str] = mapped_column(String(16), nullable=False)
    host: Mapped[str] = mapped_column(String(253), nullable=False)
    port: Mapped[int] = mapped_column(Integer, nullable=False, server_default=sa_text("0"))
    reason: Mapped[str] = mapped_column(String(24), nullable=False)
    mode: Mapped[str] = mapped_column(String(8), nullable=False)
    bucket_start: Mapped[datetime] = _ts()
    first_at: Mapped[datetime] = _ts()
    last_at: Mapped[datetime] = _ts()
    count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=sa_text("1"))


class PlatformLicence(Base):
    """An uploaded licence: the payload and signature exactly as signed. Validity is RE-VERIFIED on every read
    (the stored row carries no 'valid' flag to trust)."""

    __tablename__ = "platform_licences"

    id: Mapped[uuid.UUID] = _uuid(primary_key=True, default=uuid.uuid4)
    licence_id: Mapped[str] = mapped_column(String(64), nullable=False)
    key_id: Mapped[str] = mapped_column(String(40), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    signature: Mapped[str] = mapped_column(String(100), nullable=False)
    is_current: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=sa_text("false"))
    uploaded_by_user_id: Mapped[Optional[uuid.UUID]] = _user_fk()
    uploaded_at: Mapped[datetime] = _ts(server_now=True)


class DrHeartbeat(Base):
    """Written every minute on the primary; after a restore, the newest heartbeat present bounds the data lost."""

    __tablename__ = "dr_heartbeats"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    beat_at: Mapped[datetime] = _ts(server_now=True)
    node: Mapped[str] = mapped_column(String(64), nullable=False)


class DrDrill(Base):
    """A measured recovery drill: what was restored, to when, how much was lost (RPO) and how long it took (RTO)."""

    __tablename__ = "dr_drills"

    id: Mapped[uuid.UUID] = _uuid(primary_key=True, default=uuid.uuid4)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    outcome: Mapped[str] = mapped_column(String(8), nullable=False)
    started_at: Mapped[datetime] = _ts()
    finished_at: Mapped[datetime] = _ts()
    target_time: Mapped[Optional[datetime]] = _ts(nullable=True)
    recovered_to: Mapped[Optional[datetime]] = _ts(nullable=True)
    rpo_seconds: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 3), nullable=True)
    rto_seconds: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 3), nullable=True)
    host: Mapped[str] = mapped_column(String(128), nullable=False, server_default=sa_text("''"))
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default=sa_text("'{}'::jsonb"))

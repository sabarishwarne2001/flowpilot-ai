"""ARCH49-S1:models — the object-centric event log, SLA prediction and the exception agent.

The schema (every CHECK, the composite FKs, the partial UNIQUE on live
proposals, the "only calibrated kinds apply themselves" CHECK) lives in
alembic/versions/arch49_step1_process_intelligence.py; these mappings are the
ORM view with column types matching it exactly (verify_arch49 D2 compares).

`object_id` and `subject_id` carry no foreign key: an object is polymorphic
across six source tables and a review item across nine (models/review.py gives
the same reason for assignments). Every write resolves its subject through the
workspace-scoped read of its own table first.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any, Optional

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy import text as sa_text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID as PgUUID
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


def _json(default: str) -> Any:
    return mapped_column(JSONB, nullable=False, server_default=sa_text(f"'{default}'::jsonb"))


class ProcessEvent(Base):
    __tablename__ = "process_events"

    id: Mapped[uuid.UUID] = _uuid(primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = _uuid(nullable=False)
    workspace_id: Mapped[uuid.UUID] = _uuid(nullable=False)
    source: Mapped[str] = mapped_column(String(16), nullable=False)
    source_key: Mapped[str] = mapped_column(String(200), nullable=False)
    activity: Mapped[str] = mapped_column(String(64), nullable=False)
    occurred_at: Mapped[datetime] = _ts()
    actor_kind: Mapped[str] = mapped_column(String(8), nullable=False)
    actor_user_id: Mapped[Optional[uuid.UUID]] = _user_fk()
    attributes: Mapped[dict[str, Any]] = _json("{}")
    ingested_at: Mapped[datetime] = _ts(server_now=True)


class ProcessEventObject(Base):
    __tablename__ = "process_event_objects"

    event_id: Mapped[uuid.UUID] = _uuid(primary_key=True)
    workspace_id: Mapped[uuid.UUID] = _uuid(nullable=False)
    object_type: Mapped[str] = mapped_column(String(16), primary_key=True)
    object_id: Mapped[uuid.UUID] = _uuid(primary_key=True)
    qualifier: Mapped[str] = mapped_column(String(32), nullable=False, server_default=sa_text("''"))
    occurred_at: Mapped[datetime] = _ts()
    activity: Mapped[str] = mapped_column(String(64), nullable=False)


class ProcessIngestCursor(Base):
    __tablename__ = "process_ingest_cursors"

    workspace_id: Mapped[uuid.UUID] = _uuid(ForeignKey("workspaces.id", ondelete="CASCADE"), primary_key=True)
    source: Mapped[str] = mapped_column(String(16), primary_key=True)
    watermark: Mapped[datetime] = _ts()
    watermark_key: Mapped[str] = mapped_column(String(200), nullable=False, server_default=sa_text("''"))
    caught_up: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=sa_text("true"))
    last_run_at: Mapped[datetime] = _ts()
    rows_read: Mapped[int] = mapped_column(Integer, nullable=False, server_default=sa_text("0"))
    events_written: Mapped[int] = mapped_column(Integer, nullable=False, server_default=sa_text("0"))


class ProcessSlaPolicy(Base):
    __tablename__ = "process_sla_policies"

    workspace_id: Mapped[uuid.UUID] = _uuid(ForeignKey("workspaces.id", ondelete="CASCADE"), primary_key=True)
    object_type: Mapped[str] = mapped_column(String(16), primary_key=True)
    target_hours: Mapped[int] = mapped_column(Integer, nullable=False)
    at_risk_probability: Mapped[Decimal] = mapped_column(Numeric(4, 3), nullable=False, server_default=sa_text("0.5"))
    alerts_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=sa_text("true"))
    updated_by_user_id: Mapped[Optional[uuid.UUID]] = _user_fk()
    updated_at: Mapped[datetime] = _ts(server_now=True)


class ProcessModelRun(Base):
    __tablename__ = "process_model_runs"

    id: Mapped[uuid.UUID] = _uuid(primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = _uuid(nullable=False)
    workspace_id: Mapped[uuid.UUID] = _uuid(nullable=False)
    object_type: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(10), nullable=False)
    reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    target_hours: Mapped[int] = mapped_column(Integer, nullable=False)
    instances_train: Mapped[int] = mapped_column(Integer, nullable=False, server_default=sa_text("0"))
    instances_holdout: Mapped[int] = mapped_column(Integer, nullable=False, server_default=sa_text("0"))
    snapshots_train: Mapped[int] = mapped_column(Integer, nullable=False, server_default=sa_text("0"))
    snapshots_holdout: Mapped[int] = mapped_column(Integer, nullable=False, server_default=sa_text("0"))
    base_rate: Mapped[Optional[Decimal]] = mapped_column(Numeric(6, 5), nullable=True)
    brier: Mapped[Optional[Decimal]] = mapped_column(Numeric(8, 6), nullable=True)
    brier_baseline: Mapped[Optional[Decimal]] = mapped_column(Numeric(8, 6), nullable=True)
    skill: Mapped[Optional[Decimal]] = mapped_column(Numeric(9, 5), nullable=True)
    reliability: Mapped[list[Any]] = _json("[]")
    importances: Mapped[dict[str, Any]] = _json("{}")
    engine_version: Mapped[str] = mapped_column(String(16), nullable=False)
    trained_at: Mapped[datetime] = _ts()


class ProcessPrediction(Base):
    __tablename__ = "process_predictions"

    workspace_id: Mapped[uuid.UUID] = _uuid(primary_key=True)
    organization_id: Mapped[uuid.UUID] = _uuid(nullable=False)
    object_type: Mapped[str] = mapped_column(String(16), primary_key=True)
    object_id: Mapped[uuid.UUID] = _uuid(primary_key=True)
    run_id: Mapped[uuid.UUID] = _uuid(ForeignKey("process_model_runs.id", ondelete="CASCADE"), nullable=False)
    kind: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    started_at: Mapped[datetime] = _ts()
    due_at: Mapped[datetime] = _ts()
    probability: Mapped[Decimal] = mapped_column(Numeric(6, 5), nullable=False)
    state: Mapped[str] = mapped_column(String(10), nullable=False)
    predicted_at: Mapped[datetime] = _ts()
    alerted_at: Mapped[Optional[datetime]] = _ts(nullable=True)


class AgentPolicy(Base):
    __tablename__ = "agent_policies"

    workspace_id: Mapped[uuid.UUID] = _uuid(primary_key=True)
    organization_id: Mapped[uuid.UUID] = _uuid(nullable=False)
    planning_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=sa_text("true"))
    auto_apply_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=sa_text("false"))
    auto_apply_kinds: Mapped[list[str]] = mapped_column(ARRAY(String(40)), nullable=False,
                                                        server_default=sa_text("'{}'"))
    hold_minutes: Mapped[int] = mapped_column(Integer, nullable=False, server_default=sa_text("30"))
    enabled_by_user_id: Mapped[Optional[uuid.UUID]] = _user_fk()
    enabled_at: Mapped[Optional[datetime]] = _ts(nullable=True)
    updated_by_user_id: Mapped[Optional[uuid.UUID]] = _user_fk()
    updated_at: Mapped[datetime] = _ts(server_now=True)


class AgentProposal(Base):
    __tablename__ = "agent_proposals"

    id: Mapped[uuid.UUID] = _uuid(primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = _uuid(nullable=False)
    workspace_id: Mapped[uuid.UUID] = _uuid(nullable=False)
    subject_type: Mapped[str] = mapped_column(String(12), nullable=False)
    subject_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    subject_id: Mapped[uuid.UUID] = _uuid(nullable=False)
    work_item_id: Mapped[Optional[uuid.UUID]] = _uuid(nullable=True)
    proposal_kind: Mapped[str] = mapped_column(String(40), nullable=False)
    action: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    subject_version: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=sa_text("0"))
    confidence: Mapped[Decimal] = mapped_column(Numeric(6, 5), nullable=False)
    calibrated_probability: Mapped[Optional[Decimal]] = mapped_column(Numeric(6, 5), nullable=True)
    calibration_model_id: Mapped[Optional[uuid.UUID]] = _uuid(nullable=True)
    evidence: Mapped[dict[str, Any]] = _json("{}")
    rationale: Mapped[list[Any]] = _json("[]")
    autonomy: Mapped[dict[str, Any]] = _json("{}")
    injection_flags: Mapped[dict[str, Any]] = _json("{}")
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default=sa_text("'PROPOSED'"))
    apply_after: Mapped[Optional[datetime]] = _ts(nullable=True)
    waiting_until: Mapped[Optional[datetime]] = _ts(nullable=True)
    decided_by_user_id: Mapped[Optional[uuid.UUID]] = _user_fk()
    decided_at: Mapped[Optional[datetime]] = _ts(nullable=True)
    reject_reason: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    applied_at: Mapped[Optional[datetime]] = _ts(nullable=True)
    applied_as_user_id: Mapped[Optional[uuid.UUID]] = _user_fk()
    resolution: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    failure: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)
    engine_version: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = _ts(server_now=True)
    updated_at: Mapped[datetime] = _ts(server_now=True)


class AgentToolCall(Base):
    __tablename__ = "agent_tool_calls"

    id: Mapped[uuid.UUID] = _uuid(primary_key=True, default=uuid.uuid4)
    proposal_id: Mapped[uuid.UUID] = _uuid(nullable=False)
    workspace_id: Mapped[uuid.UUID] = _uuid(nullable=False)
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    tool: Mapped[str] = mapped_column(String(40), nullable=False)
    arguments: Mapped[dict[str, Any]] = _json("{}")
    outcome: Mapped[str] = mapped_column(String(8), nullable=False)
    detail: Mapped[dict[str, Any]] = _json("{}")
    created_at: Mapped[datetime] = _ts(server_now=True)


__all__ = ["AgentPolicy", "AgentProposal", "AgentToolCall", "ProcessEvent", "ProcessEventObject",
           "ProcessIngestCursor", "ProcessModelRun", "ProcessPrediction", "ProcessSlaPolicy"]

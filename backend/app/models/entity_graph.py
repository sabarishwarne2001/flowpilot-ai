"""ARCH42-S1:models — the entity graph. The schema's reasoning lives in
alembic/versions/arch42_step1_entity_graph.py; this module maps it."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any, Optional

from pgvector.sqlalchemy import Vector
from sqlalchemy import ARRAY, Boolean, Computed, DateTime, Float, Integer, Numeric, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

EMBEDDING_DIM = 256


def _uuid(**kw: Any) -> Any:
    return mapped_column(PgUUID(as_uuid=True), **kw)


def _ts(nullable: bool = False) -> Any:
    if nullable:
        return mapped_column(DateTime(timezone=True), nullable=True)
    return mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class EntityMatchModel(Base):
    __tablename__ = "entity_match_models"

    id: Mapped[uuid.UUID] = _uuid(primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = _uuid(nullable=False)
    workspace_id: Mapped[uuid.UUID] = _uuid(nullable=False)
    entity_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(8), nullable=False, default="ACTIVE")
    source: Mapped[str] = mapped_column(String(8), nullable=False)
    parameters: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    pair_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    iterations: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    converged: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    log_likelihood: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    fitted_at: Mapped[datetime] = _ts()


class Entity(Base):
    __tablename__ = "entities"

    id: Mapped[uuid.UUID] = _uuid(primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = _uuid(nullable=False)
    workspace_id: Mapped[uuid.UUID] = _uuid(nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(8), nullable=False, default="ACTIVE")
    display_name: Mapped[str] = mapped_column(String(300), nullable=False)
    normalized_name: Mapped[str] = mapped_column(String(300), nullable=False)
    name_embedding: Mapped[Optional[list[float]]] = mapped_column(Vector(EMBEDDING_DIM), nullable=True)
    merged_into_id: Mapped[Optional[uuid.UUID]] = _uuid(nullable=True)
    merged_at: Mapped[Optional[datetime]] = _ts(nullable=True)
    merged_by_user_id: Mapped[Optional[uuid.UUID]] = _uuid(nullable=True)
    merge_reason: Mapped[Optional[str]] = mapped_column(String(12), nullable=True)
    split_from_id: Mapped[Optional[uuid.UUID]] = _uuid(nullable=True)
    mention_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    first_seen_at: Mapped[Optional[datetime]] = _ts(nullable=True)
    last_seen_at: Mapped[Optional[datetime]] = _ts(nullable=True)
    created_at: Mapped[datetime] = _ts()
    updated_at: Mapped[datetime] = _ts()


class EntityIdentifier(Base):
    __tablename__ = "entity_identifiers"

    id: Mapped[uuid.UUID] = _uuid(primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = _uuid(nullable=False)
    entity_id: Mapped[uuid.UUID] = _uuid(nullable=False)
    entity_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    kind: Mapped[str] = mapped_column(String(24), nullable=False)
    value_hmac: Mapped[str] = mapped_column(String(64), nullable=False)
    key_generation: Mapped[str] = mapped_column(String(16), nullable=False)
    value_ciphertext: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    display_ciphertext: Mapped[str] = mapped_column(Text, nullable=False)
    derived: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = _ts()
    updated_at: Mapped[datetime] = _ts()


class EntityMention(Base):
    __tablename__ = "entity_mentions"

    id: Mapped[uuid.UUID] = _uuid(primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = _uuid(nullable=False)
    work_item_id: Mapped[uuid.UUID] = _uuid(nullable=False)
    entity_id: Mapped[uuid.UUID] = _uuid(nullable=False)
    entity_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    field_path: Mapped[str] = mapped_column(String(200), nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    role: Mapped[str] = mapped_column(String(48), nullable=False)
    surface_name: Mapped[str] = mapped_column(String(300), nullable=False)
    spec_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    identifier_ids: Mapped[list[uuid.UUID]] = mapped_column(ARRAY(PgUUID(as_uuid=True)), nullable=False, default=list)
    decision: Mapped[str] = mapped_column(String(10), nullable=False)
    method: Mapped[str] = mapped_column(String(12), nullable=False)
    source: Mapped[str] = mapped_column(String(8), nullable=False)
    match_probability: Mapped[Optional[Decimal]] = mapped_column(Numeric(6, 5), nullable=True)
    match_weight: Mapped[Optional[Decimal]] = mapped_column(Numeric(9, 3), nullable=True)
    decided_by_user_id: Mapped[Optional[uuid.UUID]] = _uuid(nullable=True)
    #: ARCH43-S1:superseded. Stamped when the document's packet was split: the
    #: children carry their own mentions; this one is kept, not counted.
    superseded_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    superseded_by_split_id: Mapped[Optional[uuid.UUID]] = _uuid(nullable=True)
    created_at: Mapped[datetime] = _ts()
    updated_at: Mapped[datetime] = _ts()


class EntityEdge(Base):
    __tablename__ = "entity_edges"

    id: Mapped[uuid.UUID] = _uuid(primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = _uuid(nullable=False)
    src_entity_id: Mapped[uuid.UUID] = _uuid(nullable=False)
    dst_entity_id: Mapped[uuid.UUID] = _uuid(nullable=False)
    relation: Mapped[str] = mapped_column(String(24), nullable=False)
    evidence_work_item_id: Mapped[uuid.UUID] = _uuid(nullable=False)
    created_at: Mapped[datetime] = _ts()


class EntityMergeCandidate(Base):
    __tablename__ = "entity_merge_candidates"

    id: Mapped[uuid.UUID] = _uuid(primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = _uuid(nullable=False)
    workspace_id: Mapped[uuid.UUID] = _uuid(nullable=False)
    left_entity_id: Mapped[uuid.UUID] = _uuid(nullable=False)
    right_entity_id: Mapped[uuid.UUID] = _uuid(nullable=False)
    low_entity_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), Computed("LEAST(left_entity_id, right_entity_id)", persisted=True))
    high_entity_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), Computed("GREATEST(left_entity_id, right_entity_id)", persisted=True))
    entity_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    mention_id: Mapped[Optional[uuid.UUID]] = _uuid(nullable=True)
    work_item_id: Mapped[Optional[uuid.UUID]] = _uuid(nullable=True)
    reason: Mapped[str] = mapped_column(String(10), nullable=False)
    match_probability: Mapped[Decimal] = mapped_column(Numeric(6, 5), nullable=False)
    match_weight: Mapped[Decimal] = mapped_column(Numeric(9, 3), nullable=False)
    comparison: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    conflict_kinds: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, default=list)
    status: Mapped[str] = mapped_column(String(10), nullable=False, default="OPEN")
    resolved_at: Mapped[Optional[datetime]] = _ts(nullable=True)
    resolved_by_user_id: Mapped[Optional[uuid.UUID]] = _uuid(nullable=True)
    created_at: Mapped[datetime] = _ts()


__all__ = [
    "EMBEDDING_DIM",
    "Entity",
    "EntityEdge",
    "EntityIdentifier",
    "EntityMatchModel",
    "EntityMention",
    "EntityMergeCandidate",
]

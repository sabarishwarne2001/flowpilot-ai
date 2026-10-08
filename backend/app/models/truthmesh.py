"""Phase 2 — TruthMesh, the cross-document digital twin (capability.truthmesh).

A tenant's documents are not independent. An invoice bills against a purchase order, which was
issued under a master agreement; a statement of work is governed by an MSA; a clinical note supports
an insurance claim made under a policy; a waybill is declared in a customs manifest. TruthMesh keeps
that structure as a graph and keeps it honest:

  mesh_nodes        one per document: its kind and rank in the hierarchy, the typed facts read from
                    its extraction (amounts, dates, parties, identifiers, contractual terms), its
                    semantic centroid (the mean of its chunk embeddings, pgvector) and a risk score
  mesh_links        bidirectional, typed links between two documents (BILLS_AGAINST, GOVERNED_BY,
                    VERSION_OF, ...), each with the signals that support it (a shared identifier, a
                    shared party, semantic similarity) and a strength in [0, 1]; a person can confirm
                    or reject a link
  mesh_conflicts    discrepancies the graph exposes: amounts beyond the authority of the parent
                    document, cumulative overruns, contradictory dates, changed payee accounts,
                    conflicting terms, duplicate billing, commitments with no authorising document
  mesh_simulations  saved what-if runs ("delivery delayed 14 days", "clause 8.2 invoked") with the
                    ripple they produce across dependent documents
  mesh_states       per workspace: when the mesh was last built and its headline numbers

Everything is computed on the platform's own stack at no external cost: stored chunk embeddings,
deterministic parsers and graph traversal. No model is called.

The schema lives in alembic/versions/p9a1_truthmesh_engine.py; these mappings declare every
constraint and index so the drift gate sees none. Composite foreign keys onto work_items
(id, workspace_id) keep a node, link or conflict from ever naming another workspace's document.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Optional

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    PrimaryKeyConstraint,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

#: Width of `document_chunks.embedding` (arch11_step2_chunks_expand); a centroid is their mean.
MESH_EMBEDDING_DIMENSION = 384

LINK_STATUSES: tuple[str, ...] = ("AUTO", "CONFIRMED", "REJECTED")
CONFLICT_STATUSES: tuple[str, ...] = ("OPEN", "ACKNOWLEDGED", "RESOLVED", "DISMISSED")
CONFLICT_SEVERITIES: tuple[str, ...] = ("CRITICAL", "HIGH", "MEDIUM", "LOW")
STATE_STATUSES: tuple[str, ...] = ("EMPTY", "BUILDING", "READY", "FAILED")


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


def _ts(nullable: bool = False) -> Any:
    if nullable:
        return mapped_column(DateTime(timezone=True), nullable=True)
    return mapped_column(DateTime(timezone=True), nullable=False, server_default=text("now()"))


class MeshNode(Base):
    """One document's twin: what it is, what it states, where it sits."""

    __tablename__ = "mesh_nodes"

    __table_args__ = (
        PrimaryKeyConstraint("id", name="pk_mesh_nodes"),
        UniqueConstraint("work_item_id", name="uq_mesh_nodes_work_item"),
        CheckConstraint("rank >= 0 AND rank <= 9", name="ck_mesh_nodes_rank"),
        CheckConstraint("risk_score >= 0 AND risk_score <= 100", name="ck_mesh_nodes_risk_range"),
        CheckConstraint("jsonb_typeof(facts) = 'array'", name="ck_mesh_nodes_facts_array"),
        CheckConstraint("jsonb_typeof(terms) = 'object'", name="ck_mesh_nodes_terms_object"),
        CheckConstraint("currency IS NULL OR currency ~ '^[A-Z]{3}$'", name="ck_mesh_nodes_currency_shape"),
        Index("ix_mesh_nodes_workspace_kind", "workspace_id", "kind"),
        Index("ix_mesh_nodes_workspace_risk", "workspace_id", "risk_score"),
        ForeignKeyConstraint(
            ["work_item_id", "workspace_id"], ["work_items.id", "work_items.workspace_id"],
            name="fk_mesh_nodes_work_item_workspace", ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"],
            name="fk_mesh_nodes_organization_id_organizations", ondelete="CASCADE",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    workspace_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    work_item_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    kind_label: Mapped[str] = mapped_column(String(80), nullable=False)
    rank: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default=text("0"))
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    document_number: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    counterparty: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)
    currency: Mapped[Optional[str]] = mapped_column(String(3), nullable=True)
    amount_micros: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    net_amount_micros: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    document_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    effective_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    end_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    facts: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb"))
    terms: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    identifiers: Mapped[list[str]] = mapped_column(
        ARRAY(String(128)), nullable=False, default=list, server_default=text("'{}'")
    )
    referenced_identifiers: Mapped[list[str]] = mapped_column(
        ARRAY(String(128)), nullable=False, default=list, server_default=text("'{}'")
    )
    parties: Mapped[list[str]] = mapped_column(
        ARRAY(String(300)), nullable=False, default=list, server_default=text("'{}'")
    )
    #: Mean of the document's chunk embeddings. Added by raw DDL in the migration (pgvector).
    centroid: Mapped[Optional[Any]] = mapped_column(Vector(MESH_EMBEDDING_DIMENSION), nullable=True)
    risk_score: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False, server_default=text("0"))
    degree: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    indexed_at: Mapped[datetime] = _ts()
    created_at: Mapped[datetime] = _ts()
    updated_at: Mapped[datetime] = _ts()


class MeshLink(Base):
    """A typed, bidirectional link between two documents, with its evidence.

    For a directed relation (an invoice BILLS_AGAINST a purchase order) `source` is the dependent
    document and `target` the one it depends on. For an undirected relation (RELATES_TO,
    SHARES_PARTY, VERSION_OF) `source` is the lower id. One row per pair.
    """

    __tablename__ = "mesh_links"

    __table_args__ = (
        PrimaryKeyConstraint("id", name="pk_mesh_links"),
        UniqueConstraint("workspace_id", "source_work_item_id", "target_work_item_id", name="uq_mesh_links_pair"),
        CheckConstraint("source_work_item_id <> target_work_item_id", name="ck_mesh_links_not_self"),
        CheckConstraint("strength >= 0 AND strength <= 1", name="ck_mesh_links_strength_ratio"),
        CheckConstraint(_in("status", LINK_STATUSES), name="ck_mesh_links_status"),
        CheckConstraint("jsonb_typeof(signals) = 'array'", name="ck_mesh_links_signals_array"),
        Index("ix_mesh_links_workspace_source", "workspace_id", "source_work_item_id"),
        Index("ix_mesh_links_workspace_target", "workspace_id", "target_work_item_id"),
        ForeignKeyConstraint(
            ["source_work_item_id", "workspace_id"], ["work_items.id", "work_items.workspace_id"],
            name="fk_mesh_links_source_workspace", ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["target_work_item_id", "workspace_id"], ["work_items.id", "work_items.workspace_id"],
            name="fk_mesh_links_target_workspace", ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"],
            name="fk_mesh_links_organization_id_organizations", ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["decided_by_user_id"], ["users.id"],
            name="fk_mesh_links_decided_by_user_id_users", ondelete="SET NULL",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    workspace_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    source_work_item_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    target_work_item_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    relation: Mapped[str] = mapped_column(String(24), nullable=False)
    directed: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    strength: Mapped[Decimal] = mapped_column(Numeric(5, 4), nullable=False)
    method: Mapped[str] = mapped_column(String(12), nullable=False)
    signals: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    status: Mapped[str] = mapped_column(String(10), nullable=False, server_default=text("'AUTO'"))
    decided_by_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(PgUUID(as_uuid=True), nullable=True)
    decided_at: Mapped[Optional[datetime]] = _ts(nullable=True)
    created_at: Mapped[datetime] = _ts()
    updated_at: Mapped[datetime] = _ts()


class MeshConflict(Base):
    """A discrepancy across documents, kept across rebuilds by its fingerprint."""

    __tablename__ = "mesh_conflicts"

    __table_args__ = (
        PrimaryKeyConstraint("id", name="pk_mesh_conflicts"),
        UniqueConstraint("workspace_id", "fingerprint", name="uq_mesh_conflicts_fingerprint"),
        CheckConstraint(_in("status", CONFLICT_STATUSES), name="ck_mesh_conflicts_status"),
        CheckConstraint(_in("severity", CONFLICT_SEVERITIES), name="ck_mesh_conflicts_severity"),
        CheckConstraint("cardinality(work_item_ids) >= 1", name="ck_mesh_conflicts_documents"),
        CheckConstraint("jsonb_typeof(document_values) = 'array'", name="ck_mesh_conflicts_values_array"),
        CheckConstraint("jsonb_typeof(details) = 'object'", name="ck_mesh_conflicts_details_object"),
        CheckConstraint("exposure_micros IS NULL OR exposure_micros >= 0", name="ck_mesh_conflicts_exposure"),
        CheckConstraint("currency IS NULL OR currency ~ '^[A-Z]{3}$'", name="ck_mesh_conflicts_currency_shape"),
        CheckConstraint(
            "status NOT IN ('RESOLVED', 'DISMISSED') OR resolved_at IS NOT NULL",
            name="ck_mesh_conflicts_resolution_recorded",
        ),
        Index("ix_mesh_conflicts_workspace_status", "workspace_id", "status", "severity"),
        Index("ix_mesh_conflicts_documents", "work_item_ids", postgresql_using="gin"),
        ForeignKeyConstraint(
            ["workspace_id"], ["workspaces.id"],
            name="fk_mesh_conflicts_workspace_id_workspaces", ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"],
            name="fk_mesh_conflicts_organization_id_organizations", ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["resolved_by_user_id"], ["users.id"],
            name="fk_mesh_conflicts_resolved_by_user_id_users", ondelete="SET NULL",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    workspace_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    concept: Mapped[str] = mapped_column(String(64), nullable=False)
    severity: Mapped[str] = mapped_column(String(8), nullable=False)
    status: Mapped[str] = mapped_column(String(12), nullable=False, server_default=text("'OPEN'"))
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    relation: Mapped[Optional[str]] = mapped_column(String(24), nullable=True)
    work_item_ids: Mapped[list[uuid.UUID]] = mapped_column(ARRAY(PgUUID(as_uuid=True)), nullable=False)
    document_values: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    details: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    exposure_micros: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    currency: Mapped[Optional[str]] = mapped_column(String(3), nullable=True)
    auto_resolved: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    resolution_note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    resolved_by_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(PgUUID(as_uuid=True), nullable=True)
    resolved_at: Mapped[Optional[datetime]] = _ts(nullable=True)
    first_seen_at: Mapped[datetime] = _ts()
    last_seen_at: Mapped[datetime] = _ts()
    created_at: Mapped[datetime] = _ts()
    updated_at: Mapped[datetime] = _ts()


class MeshSimulation(Base):
    """A saved what-if: the condition, where it started and the ripple it produced."""

    __tablename__ = "mesh_simulations"

    __table_args__ = (
        PrimaryKeyConstraint("id", name="pk_mesh_simulations"),
        CheckConstraint("jsonb_typeof(parameters) = 'object'", name="ck_mesh_simulations_parameters_object"),
        CheckConstraint("jsonb_typeof(result) = 'object'", name="ck_mesh_simulations_result_object"),
        Index("ix_mesh_simulations_workspace_created", "workspace_id", "created_at"),
        ForeignKeyConstraint(
            ["origin_work_item_id", "workspace_id"], ["work_items.id", "work_items.workspace_id"],
            name="fk_mesh_simulations_origin_workspace", ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"],
            name="fk_mesh_simulations_organization_id_organizations", ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["created_by_user_id"], ["users.id"],
            name="fk_mesh_simulations_created_by_user_id_users", ondelete="SET NULL",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    workspace_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    origin_work_item_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    created_by_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(PgUUID(as_uuid=True), nullable=True)
    scenario: Mapped[str] = mapped_column(String(24), nullable=False)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    parameters: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    result: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    documents_affected: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    exposure_micros: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    currency: Mapped[Optional[str]] = mapped_column(String(3), nullable=True)
    created_at: Mapped[datetime] = _ts()


class MeshState(Base):
    """A workspace's mesh: built when, how big, how risky."""

    __tablename__ = "mesh_states"

    __table_args__ = (
        PrimaryKeyConstraint("workspace_id", name="pk_mesh_states"),
        CheckConstraint(_in("status", STATE_STATUSES), name="ck_mesh_states_status"),
        CheckConstraint("risk_index >= 0 AND risk_index <= 100", name="ck_mesh_states_risk_range"),
        ForeignKeyConstraint(
            ["workspace_id"], ["workspaces.id"],
            name="fk_mesh_states_workspace_id_workspaces", ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"],
            name="fk_mesh_states_organization_id_organizations", ondelete="CASCADE",
        ),
    )

    workspace_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    organization_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    status: Mapped[str] = mapped_column(String(10), nullable=False, server_default=text("'EMPTY'"))
    nodes: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    links: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    open_conflicts: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    risk_index: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False, server_default=text("0"))
    build_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    engine_version: Mapped[Optional[str]] = mapped_column(String(24), nullable=True)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    last_built_at: Mapped[Optional[datetime]] = _ts(nullable=True)
    updated_at: Mapped[datetime] = _ts()


__all__ = [
    "CONFLICT_SEVERITIES",
    "CONFLICT_STATUSES",
    "LINK_STATUSES",
    "MESH_EMBEDDING_DIMENSION",
    "MeshConflict",
    "MeshLink",
    "MeshNode",
    "MeshSimulation",
    "MeshState",
    "STATE_STATUSES",
]

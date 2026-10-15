"""Helm — the ORM mapping of migration `h1a1_helm` (docs/helm/ARCHITECTURE.md §3).

Every table carries `organization_id`. A table tied to documents carries `workspace_id` too, with
the ARCH-41 composite keys: `(workspace_id, organization_id)` onto `workspaces` and
`(work_item_id, workspace_id)` onto `work_items`, so a row naming another tenant's document is
refused by PostgreSQL, not by a filter. The constraints are declared here as well as in the
migration so autogenerate sees no drift (`scripts/check_migration_drift.py`).

Two tables are append-only: `helm_mandate_versions` and `helm_certificates` refuse UPDATE in the
database (trigger `helm_refuse_update`), because a plan or a certificate names the version it used
and that version must not change underneath it.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any, Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.services.helm import vocabulary as v


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid.uuid4,
                         server_default=text("gen_random_uuid()"))


def _org() -> Mapped[uuid.UUID]:
    return mapped_column(PgUUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False)


def _user(nullable: bool = True) -> Mapped[Optional[uuid.UUID]]:
    return mapped_column(PgUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=nullable)


def _ts(nullable: bool = False) -> Mapped[Any]:
    if nullable:
        return mapped_column(DateTime(timezone=True), nullable=True)
    return mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


def _book_fk(table: str) -> ForeignKeyConstraint:
    return ForeignKeyConstraint(
        ["book_id", "organization_id"], ["helm_books.id", "helm_books.organization_id"],
        name=f"fk_{table}_book_org", ondelete="CASCADE",
    )


def _workspace_fk(table: str) -> ForeignKeyConstraint:
    return ForeignKeyConstraint(
        ["workspace_id", "organization_id"], ["workspaces.id", "workspaces.organization_id"],
        name=f"fk_{table}_workspace_org", ondelete="CASCADE",
    )


def _work_item_fk(table: str) -> ForeignKeyConstraint:
    return ForeignKeyConstraint(
        ["work_item_id", "workspace_id"], ["work_items.id", "work_items.workspace_id"],
        name=f"fk_{table}_work_item_workspace", ondelete="CASCADE",
    )


class HelmBook(Base):
    """One company Helm keeps inside its mandates: a workspace, every workspace, or the sample."""

    __tablename__ = "helm_books"
    __table_args__ = (
        UniqueConstraint("id", "organization_id", name="uq_helm_books_id_org"),
        _workspace_fk("helm_books"),
        CheckConstraint(f"kind IN ({v.sql_in(v.BOOK_KINDS)})", name="ck_helm_books_kind"),
        CheckConstraint(f"status IN ({v.sql_in(v.BOOK_STATUSES)})", name="ck_helm_books_status"),
        CheckConstraint("(kind = 'ORGANIZATION') = (workspace_id IS NULL)", name="ck_helm_books_scope"),
        Index("ix_helm_books_organization", "organization_id"),
        Index("uq_helm_books_live_workspace", "workspace_id", unique=True,
              postgresql_where=text("workspace_id IS NOT NULL AND status = 'ACTIVE'")),
        Index("uq_helm_books_live_organization", "organization_id", unique=True,
              postgresql_where=text("kind = 'ORGANIZATION' AND status = 'ACTIVE'")),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    organization_id: Mapped[uuid.UUID] = _org()
    workspace_id: Mapped[Optional[uuid.UUID]] = mapped_column(PgUUID(as_uuid=True), nullable=True)
    kind: Mapped[str] = mapped_column(String(12), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str] = mapped_column(String(10), nullable=False, server_default=text("'ACTIVE'"))
    created_by_user_id: Mapped[Optional[uuid.UUID]] = _user()
    created_at: Mapped[datetime] = _ts()
    updated_at: Mapped[datetime] = _ts()


class HelmSettings(Base):
    """A book's settings: who "we" are, when payments run, the money rules the Planner uses."""

    __tablename__ = "helm_settings"
    __table_args__ = (
        _book_fk("helm_settings"),
        CheckConstraint("horizon_days BETWEEN 7 AND 90", name="ck_helm_settings_horizon"),
        CheckConstraint("cost_of_capital_bps BETWEEN 0 AND 10000", name="ck_helm_settings_cost_of_capital"),
        CheckConstraint(f"default_autonomy IN ({v.sql_in(v.AUTONOMY_MODES)})", name="ck_helm_settings_autonomy"),
        CheckConstraint("jsonb_typeof(identities) = 'array'", name="ck_helm_settings_identities_array"),
    )

    book_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    organization_id: Mapped[uuid.UUID] = _org()
    #: [{"name": ..., "tax_id": ..., "tax_kind": "GSTIN"|"VAT"|"EIN"|..., "source": "SUGGESTED"|"PERSON"}]
    identities: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    identities_confirmed_at: Mapped[Optional[datetime]] = _ts(nullable=True)
    identities_confirmed_by_user_id: Mapped[Optional[uuid.UUID]] = _user()
    #: ISO weekdays (Monday 1 … Sunday 7) on which payments are released.
    run_weekdays: Mapped[list[int]] = mapped_column(ARRAY(SmallInteger), nullable=False,
                                                    server_default=text("'{2,5}'"))
    horizon_days: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default=text("30"))
    cost_of_capital_bps: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1200"))
    default_autonomy: Mapped[str] = mapped_column(String(8), nullable=False, server_default=text("'propose'"))
    briefing_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    business_type: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    first_run_step: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default=text("0"))
    first_run_completed_at: Mapped[Optional[datetime]] = _ts(nullable=True)
    preferences: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    updated_by_user_id: Mapped[Optional[uuid.UUID]] = _user()
    created_at: Mapped[datetime] = _ts()
    updated_at: Mapped[datetime] = _ts()


class HelmCounterparty(Base):
    """A supplier or customer of one book: who they are, MSME status, contact, behaviour."""

    __tablename__ = "helm_counterparties"
    __table_args__ = (
        UniqueConstraint("book_id", "key", name="uq_helm_counterparties_book_key"),
        _book_fk("helm_counterparties"),
        CheckConstraint(f"role IN ({v.sql_in(v.COUNTERPARTY_ROLES)})", name="ck_helm_counterparties_role"),
        CheckConstraint(f"msme_category IS NULL OR msme_category IN ({v.sql_in(v.MSME_CATEGORIES)})",
                        name="ck_helm_counterparties_msme_category"),
        CheckConstraint(f"msme_source IS NULL OR msme_source IN ({v.sql_in(v.MSME_SOURCES)})",
                        name="ck_helm_counterparties_msme_source"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    organization_id: Mapped[uuid.UUID] = _org()
    book_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    key: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(10), nullable=False)
    display_name: Mapped[str] = mapped_column(String(300), nullable=False)
    tax_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    entity_ids: Mapped[list[uuid.UUID]] = mapped_column(ARRAY(PgUUID(as_uuid=True)), nullable=False,
                                                        server_default=text("'{}'"))
    udyam_number: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    #: NULL = not known to be an MSME; with a Udyam number and no confirmation Helm reads it as
    #: micro/small (the conservative choice) and says "category unconfirmed".
    msme_category: Mapped[Optional[str]] = mapped_column(String(8), nullable=True)
    msme_source: Mapped[Optional[str]] = mapped_column(String(12), nullable=True)
    msme_confirmed_by_user_id: Mapped[Optional[uuid.UUID]] = _user()
    msme_confirmed_at: Mapped[Optional[datetime]] = _ts(nullable=True)
    contact_email: Mapped[Optional[str]] = mapped_column(String(320), nullable=True)
    contact_name: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    behaviour: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    created_at: Mapped[datetime] = _ts()
    updated_at: Mapped[datetime] = _ts()


class HelmSettlement(Base):
    """Whether one invoice was paid (or received), with the evidence. A source of truth: a
    person's "paid" or "held" cannot be rebuilt from documents, so the ledger reads it."""

    __tablename__ = "helm_settlements"
    __table_args__ = (
        UniqueConstraint("work_item_id", name="uq_helm_settlements_work_item"),
        _workspace_fk("helm_settlements"),
        _work_item_fk("helm_settlements"),
        CheckConstraint(f"state IN ({v.sql_in(v.SETTLEMENT_STATES)})", name="ck_helm_settlements_state"),
        CheckConstraint(f"source IN ({v.sql_in(v.SETTLEMENT_SOURCES)})", name="ck_helm_settlements_source"),
        CheckConstraint("state <> 'PAID' OR paid_on IS NOT NULL", name="ck_helm_settlements_paid_has_date"),
        CheckConstraint("state NOT IN ('SCHEDULED', 'APPROVED') OR run_date IS NOT NULL",
                        name="ck_helm_settlements_scheduled_has_date"),
        Index("ix_helm_settlements_workspace_state", "workspace_id", "state"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    organization_id: Mapped[uuid.UUID] = _org()
    workspace_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    work_item_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    state: Mapped[str] = mapped_column(String(10), nullable=False)
    run_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    paid_on: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    paid_amount_micros: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    paid_reference: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    hold_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    source: Mapped[str] = mapped_column(String(10), nullable=False)
    evidence: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    action_id: Mapped[Optional[uuid.UUID]] = mapped_column(PgUUID(as_uuid=True), nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    updated_by_user_id: Mapped[Optional[uuid.UUID]] = _user()
    created_at: Mapped[datetime] = _ts()
    updated_at: Mapped[datetime] = _ts()


class HelmCashPosition(Base):
    """Cash on hand in one currency on a date: a statement's closing balance or a typed figure."""

    __tablename__ = "helm_cash_positions"
    __table_args__ = (
        _book_fk("helm_cash_positions"),
        _work_item_fk("helm_cash_positions"),
        CheckConstraint(f"source IN ({v.sql_in(v.CASH_SOURCES)})", name="ck_helm_cash_positions_source"),
        CheckConstraint("currency ~ '^[A-Z]{3}$'", name="ck_helm_cash_positions_currency"),
        CheckConstraint("source <> 'STATEMENT' OR work_item_id IS NOT NULL",
                        name="ck_helm_cash_positions_statement_has_document"),
        Index("ix_helm_cash_positions_book_currency", "book_id", "currency", "as_of"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    organization_id: Mapped[uuid.UUID] = _org()
    book_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    amount_micros: Mapped[int] = mapped_column(BigInteger, nullable=False)
    as_of: Mapped[date] = mapped_column(Date, nullable=False)
    source: Mapped[str] = mapped_column(String(10), nullable=False)
    workspace_id: Mapped[Optional[uuid.UUID]] = mapped_column(PgUUID(as_uuid=True), nullable=True)
    work_item_id: Mapped[Optional[uuid.UUID]] = mapped_column(PgUUID(as_uuid=True), nullable=True)
    note: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)
    entered_by_user_id: Mapped[Optional[uuid.UUID]] = _user()
    created_at: Mapped[datetime] = _ts()


class HelmPromise(Base):
    """One promise found in the documents, and whether it was kept. A rebuildable projection:
    `source_key` is deterministic, so the nightly full rebuild writes exactly these rows."""

    __tablename__ = "helm_promises"
    __table_args__ = (
        UniqueConstraint("source_key", name="uq_helm_promises_source_key"),
        _workspace_fk("helm_promises"),
        _work_item_fk("helm_promises"),
        CheckConstraint(f"kind IN ({v.sql_in(v.PROMISE_KINDS)})", name="ck_helm_promises_kind"),
        CheckConstraint(f"direction IN ({v.sql_in(v.DIRECTIONS)})", name="ck_helm_promises_direction"),
        CheckConstraint(f"status IN ({v.sql_in(v.PROMISE_STATUSES)})", name="ck_helm_promises_status"),
        CheckConstraint(f"confidence IN ({v.sql_in(v.CONFIDENCES)})", name="ck_helm_promises_confidence"),
        CheckConstraint("currency IS NULL OR currency ~ '^[A-Z]{3}$'", name="ck_helm_promises_currency"),
        Index("ix_helm_promises_workspace_kind_status", "workspace_id", "kind", "status"),
        Index("ix_helm_promises_work_item", "work_item_id"),
        Index("ix_helm_promises_counterparty", "workspace_id", "counterparty_key"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    organization_id: Mapped[uuid.UUID] = _org()
    workspace_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    work_item_id: Mapped[Optional[uuid.UUID]] = mapped_column(PgUUID(as_uuid=True), nullable=True)
    source_key: Mapped[str] = mapped_column(String(64), nullable=False)
    kind: Mapped[str] = mapped_column(String(24), nullable=False)
    direction: Mapped[str] = mapped_column(String(12), nullable=False)
    counterparty_key: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    counterparty_name: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)
    currency: Mapped[Optional[str]] = mapped_column(String(3), nullable=True)
    amount_micros: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    due_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    window_end: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    status: Mapped[str] = mapped_column(String(10), nullable=False)
    confidence: Mapped[str] = mapped_column(String(10), nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    terms: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    evidence: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    fulfilments: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    obligation_id: Mapped[Optional[uuid.UUID]] = mapped_column(PgUUID(as_uuid=True), nullable=True)
    engine_version: Mapped[str] = mapped_column(String(16), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = _ts()
    updated_at: Mapped[datetime] = _ts()


class HelmMandate(Base):
    """A mandate: an outcome or a hard limit the company states. Its spec lives in versions."""

    __tablename__ = "helm_mandates"
    __table_args__ = (
        UniqueConstraint("id", "organization_id", name="uq_helm_mandates_id_org"),
        _book_fk("helm_mandates"),
        CheckConstraint(f"status IN ({v.sql_in(v.MANDATE_STATUSES)})", name="ck_helm_mandates_status"),
        CheckConstraint("current_version >= 1", name="ck_helm_mandates_version"),
        Index("ix_helm_mandates_book_status", "book_id", "status"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    organization_id: Mapped[uuid.UUID] = _org()
    book_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    template: Mapped[str] = mapped_column(String(48), nullable=False)
    status: Mapped[str] = mapped_column(String(10), nullable=False)
    current_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    created_by_user_id: Mapped[Optional[uuid.UUID]] = _user()
    created_at: Mapped[datetime] = _ts()
    updated_at: Mapped[datetime] = _ts()
    activated_at: Mapped[Optional[datetime]] = _ts(nullable=True)


class HelmMandateVersion(Base):
    """One immutable version of a mandate's spec, with its readback. Append-only."""

    __tablename__ = "helm_mandate_versions"
    __table_args__ = (
        CheckConstraint("jsonb_typeof(spec) = 'object'", name="ck_helm_mandate_versions_spec_object"),
        CheckConstraint("spec_hash ~ '^[0-9a-f]{64}$'", name="ck_helm_mandate_versions_hash"),
    )

    mandate_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("helm_mandates.id", ondelete="CASCADE"), primary_key=True)
    version: Mapped[int] = mapped_column(Integer, primary_key=True)
    organization_id: Mapped[uuid.UUID] = _org()
    spec: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    spec_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    readback: Mapped[str] = mapped_column(Text, nullable=False)
    created_by_user_id: Mapped[Optional[uuid.UUID]] = _user()
    created_at: Mapped[datetime] = _ts()


class HelmPlan(Base):
    """One version of a book's plan. Superseded, never edited."""

    __tablename__ = "helm_plans"
    __table_args__ = (
        UniqueConstraint("book_id", "version", name="uq_helm_plans_book_version"),
        _book_fk("helm_plans"),
        CheckConstraint(f"status IN ({v.sql_in(v.PLAN_STATUSES)})", name="ck_helm_plans_status"),
        CheckConstraint(f"solver_status IN ({v.sql_in(v.SOLVER_STATUSES)})", name="ck_helm_plans_solver_status"),
        CheckConstraint(f"triggered_by IN ({v.sql_in(v.PLAN_TRIGGERS)})", name="ck_helm_plans_trigger"),
        CheckConstraint("inputs_digest ~ '^[0-9a-f]{64}$'", name="ck_helm_plans_digest"),
        Index("uq_helm_plans_current", "book_id", unique=True, postgresql_where=text("status = 'CURRENT'")),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    organization_id: Mapped[uuid.UUID] = _org()
    book_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(10), nullable=False)
    inputs_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    seed: Mapped[int] = mapped_column(BigInteger, nullable=False)
    engine_version: Mapped[str] = mapped_column(String(16), nullable=False)
    horizon_days: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    solver_status: Mapped[str] = mapped_column(String(12), nullable=False)
    conflict: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONB(none_as_null=True), nullable=True)
    summary: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    mandate_versions: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    triggered_by: Mapped[str] = mapped_column(String(10), nullable=False)
    duration_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = _ts()
    superseded_at: Mapped[Optional[datetime]] = _ts(nullable=True)


class HelmAction(Base):
    """Something Helm proposes or did: hold, schedule, chase, request, decide, tell."""

    __tablename__ = "helm_actions"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_helm_actions_idempotency_key"),
        _book_fk("helm_actions"),
        _work_item_fk("helm_actions"),
        CheckConstraint(f"kind IN ({v.sql_in(v.ACTION_KINDS)})", name="ck_helm_actions_kind"),
        CheckConstraint(f"state IN ({v.sql_in(v.ACTION_STATES)})", name="ck_helm_actions_state"),
        CheckConstraint(f"autonomy IN ({v.sql_in(v.AUTONOMY_MODES)})", name="ck_helm_actions_autonomy"),
        CheckConstraint("currency IS NULL OR currency ~ '^[A-Z]{3}$'", name="ck_helm_actions_currency"),
        CheckConstraint("required_approvals BETWEEN 1 AND 3", name="ck_helm_actions_required_approvals"),
        CheckConstraint("state <> 'REJECTED' OR rejected_reason IS NOT NULL", name="ck_helm_actions_rejected_reason"),
        Index("ix_helm_actions_book_state", "book_id", "state"),
        Index("ix_helm_actions_plan", "plan_id"),
        Index("ix_helm_actions_work_item", "work_item_id"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    organization_id: Mapped[uuid.UUID] = _org()
    book_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    plan_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("helm_plans.id", ondelete="SET NULL"), nullable=True)
    workspace_id: Mapped[Optional[uuid.UUID]] = mapped_column(PgUUID(as_uuid=True), nullable=True)
    work_item_id: Mapped[Optional[uuid.UUID]] = mapped_column(PgUUID(as_uuid=True), nullable=True)
    promise_id: Mapped[Optional[uuid.UUID]] = mapped_column(PgUUID(as_uuid=True), nullable=True)
    mandate_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("helm_mandates.id", ondelete="SET NULL"), nullable=True)
    mandate_version: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(200), nullable=False)
    state: Mapped[str] = mapped_column(String(10), nullable=False)
    autonomy: Mapped[str] = mapped_column(String(8), nullable=False)
    needs_approval: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    guard_passed: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    guard: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    amount_micros: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    currency: Mapped[Optional[str]] = mapped_column(String(3), nullable=True)
    run_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    counterparty_key: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    explanation: Mapped[str] = mapped_column(Text, nullable=False)
    value_micros: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    params: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    result: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    undoable: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    approvals: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    required_approvals: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default=text("1"))
    decided_by_user_id: Mapped[Optional[uuid.UUID]] = _user()
    decided_at: Mapped[Optional[datetime]] = _ts(nullable=True)
    rejected_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    executed_at: Mapped[Optional[datetime]] = _ts(nullable=True)
    executed_by_user_id: Mapped[Optional[uuid.UUID]] = _user()
    certificate_id: Mapped[Optional[uuid.UUID]] = mapped_column(PgUUID(as_uuid=True), nullable=True)
    created_at: Mapped[datetime] = _ts()
    updated_at: Mapped[datetime] = _ts()


class HelmCertificate(Base):
    """The canonical record of one Guard decision and its SHA-256. Append-only."""

    __tablename__ = "helm_certificates"
    __table_args__ = (
        _book_fk("helm_certificates"),
        CheckConstraint(f"stage IN ({v.sql_in(v.CERT_STAGES)})", name="ck_helm_certificates_stage"),
        CheckConstraint("sha256 ~ '^[0-9a-f]{64}$'", name="ck_helm_certificates_sha256"),
        Index("ix_helm_certificates_action", "action_id"),
        Index("ix_helm_certificates_book_created", "book_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    organization_id: Mapped[uuid.UUID] = _org()
    book_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    action_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("helm_actions.id", ondelete="CASCADE"), nullable=True)
    plan_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("helm_plans.id", ondelete="SET NULL"), nullable=True)
    stage: Mapped[str] = mapped_column(String(10), nullable=False)
    passed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    canonical: Mapped[str] = mapped_column(Text, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = _ts()


class HelmBacktest(Base):
    """One Time-machine run: what Helm would have done over a past period, and what it was worth."""

    __tablename__ = "helm_backtests"
    __table_args__ = (
        _book_fk("helm_backtests"),
        CheckConstraint(f"status IN ({v.sql_in(v.BACKTEST_STATUSES)})", name="ck_helm_backtests_status"),
        CheckConstraint("months BETWEEN 1 AND 24", name="ck_helm_backtests_months"),
        CheckConstraint("period_start <= period_end", name="ck_helm_backtests_period"),
        Index("ix_helm_backtests_book_created", "book_id", "created_at"),
        Index("ix_helm_backtests_organization_preview", "organization_id", "preview", "created_at"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    organization_id: Mapped[uuid.UUID] = _org()
    book_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    status: Mapped[str] = mapped_column(String(10), nullable=False)
    months: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)
    preview: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    mandates: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    result: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    requested_by_user_id: Mapped[Optional[uuid.UUID]] = _user()
    job_id: Mapped[Optional[uuid.UUID]] = mapped_column(PgUUID(as_uuid=True), nullable=True)
    created_at: Mapped[datetime] = _ts()
    started_at: Mapped[Optional[datetime]] = _ts(nullable=True)
    finished_at: Mapped[Optional[datetime]] = _ts(nullable=True)


class HelmValueEvent(Base):
    """One outcome Helm can prove it held, counted once (`dedupe_key`)."""

    __tablename__ = "helm_value_events"
    __table_args__ = (
        UniqueConstraint("dedupe_key", name="uq_helm_value_events_dedupe_key"),
        _book_fk("helm_value_events"),
        _work_item_fk("helm_value_events"),
        CheckConstraint(f"category IN ({v.sql_in(v.VALUE_CATEGORIES)})", name="ck_helm_value_events_category"),
        CheckConstraint("amount_micros > 0", name="ck_helm_value_events_positive"),
        CheckConstraint("currency ~ '^[A-Z]{3}$'", name="ck_helm_value_events_currency"),
        Index("ix_helm_value_events_book_month", "book_id", "month"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    organization_id: Mapped[uuid.UUID] = _org()
    book_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    workspace_id: Mapped[Optional[uuid.UUID]] = mapped_column(PgUUID(as_uuid=True), nullable=True)
    work_item_id: Mapped[Optional[uuid.UUID]] = mapped_column(PgUUID(as_uuid=True), nullable=True)
    category: Mapped[str] = mapped_column(String(24), nullable=False)
    dedupe_key: Mapped[str] = mapped_column(String(64), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    amount_micros: Mapped[int] = mapped_column(BigInteger, nullable=False)
    occurred_on: Mapped[date] = mapped_column(Date, nullable=False)
    month: Mapped[date] = mapped_column(Date, nullable=False)
    evidence: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    action_id: Mapped[Optional[uuid.UUID]] = mapped_column(PgUUID(as_uuid=True), nullable=True)
    created_at: Mapped[datetime] = _ts()


class HelmTask(Base):
    """A task Helm opened for a person (verify a bank account, decide a renewal). Listed in the
    review hub as kind HELM; resolving it there records the decision here."""

    __tablename__ = "helm_tasks"
    __table_args__ = (
        _book_fk("helm_tasks"),
        _workspace_fk("helm_tasks"),
        _work_item_fk("helm_tasks"),
        CheckConstraint(f"kind IN ({v.sql_in(v.TASK_KINDS)})", name="ck_helm_tasks_kind"),
        CheckConstraint(f"status IN ({v.sql_in(v.TASK_STATUSES)})", name="ck_helm_tasks_status"),
        CheckConstraint(f"severity IN ({v.sql_in(v.SEVERITIES)})", name="ck_helm_tasks_severity"),
        CheckConstraint("status = 'OPEN' OR resolved_at IS NOT NULL", name="ck_helm_tasks_resolved_recorded"),
        Index("ix_helm_tasks_workspace_status", "workspace_id", "status", "created_at"),
        Index("ix_helm_tasks_action", "action_id"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    organization_id: Mapped[uuid.UUID] = _org()
    workspace_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    book_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    action_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("helm_actions.id", ondelete="SET NULL"), nullable=True)
    kind: Mapped[str] = mapped_column(String(24), nullable=False)
    work_item_id: Mapped[Optional[uuid.UUID]] = mapped_column(PgUUID(as_uuid=True), nullable=True)
    obligation_id: Mapped[Optional[uuid.UUID]] = mapped_column(PgUUID(as_uuid=True), nullable=True)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    severity: Mapped[str] = mapped_column(String(8), nullable=False)
    status: Mapped[str] = mapped_column(String(10), nullable=False, server_default=text("'OPEN'"))
    decision: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    decision_note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    resolved_by_user_id: Mapped[Optional[uuid.UUID]] = _user()
    resolved_at: Mapped[Optional[datetime]] = _ts(nullable=True)
    created_at: Mapped[datetime] = _ts()
    updated_at: Mapped[datetime] = _ts()


__all__ = [
    "HelmAction",
    "HelmBacktest",
    "HelmBook",
    "HelmCashPosition",
    "HelmCertificate",
    "HelmCounterparty",
    "HelmMandate",
    "HelmMandateVersion",
    "HelmPlan",
    "HelmPromise",
    "HelmSettings",
    "HelmSettlement",
    "HelmTask",
    "HelmValueEvent",
]

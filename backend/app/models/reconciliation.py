"""ARCH-14 Step 5 — provider reconciliation."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from enum import Enum as PyEnum
from typing import Any, Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDMixin


class StatementGrain(str, PyEnum):
    DAY = "DAY"
    MONTH = "MONTH"
    INVOICE = "INVOICE"


class Attribution(str, PyEnum):
    ATTESTED = "ATTESTED"
    ALLOCATED = "ALLOCATED"
    AGGREGATE = "AGGREGATE"


class ReconciliationStatus(str, PyEnum):
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    REFUSED = "REFUSED"


class ReconciliationCategory(str, PyEnum):
    TIMING_BOUNDARY = "TIMING_BOUNDARY"
    ESTIMATE_DRIFT = "ESTIMATE_DRIFT"
    PRICE_DRIFT = "PRICE_DRIFT"
    UNMETERED_GENERATION = "UNMETERED_GENERATION"
    OVERMETERED_LEDGER = "OVERMETERED_LEDGER"
    UNEXPLAINED = "UNEXPLAINED"


CATEGORY_ORDER: tuple[ReconciliationCategory, ...] = (
    ReconciliationCategory.TIMING_BOUNDARY,
    ReconciliationCategory.ESTIMATE_DRIFT,
    ReconciliationCategory.PRICE_DRIFT,
    ReconciliationCategory.UNMETERED_GENERATION,
    ReconciliationCategory.OVERMETERED_LEDGER,
    ReconciliationCategory.UNEXPLAINED,
)


class FindingSeverity(str, PyEnum):
    INFO = "INFO"
    WARNING = "WARNING"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


DRIFT_ALERT_BPS: int = 50


class ProviderStatement(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "provider_statements"

    __table_args__ = (
        CheckConstraint("period_end > period_start", name="period_ordered"),
        CheckConstraint("length(provider) > 0", name="provider_not_blank"),
        CheckConstraint("length(currency) = 3", name="currency_iso4217"),
        CheckConstraint(
            "grain IN ('DAY', 'MONTH', 'INVOICE')", name="grain_known"
        ),
        CheckConstraint(
            "attribution IN ('ATTESTED', 'ALLOCATED', 'AGGREGATE')",
            name="attribution_known",
        ),
        CheckConstraint("line_count >= 0", name="line_count_non_negative"),
        Index(
            "uq_provider_statements_source",
            "provider",
            "source_key",
            unique=True,
        ),
        Index(
            "ix_provider_statements_period",
            "provider",
            "period_start",
            "period_end",
        ),
    )

    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    source_key: Mapped[str] = mapped_column(String(200), nullable=False)
    source_reference: Mapped[Optional[str]] = mapped_column(
        String(500), nullable=True
    )
    source_digest: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    grain: Mapped[str] = mapped_column(String(16), nullable=False)
    attribution: Mapped[str] = mapped_column(String(16), nullable=False)
    currency: Mapped[str] = mapped_column(
        String(3), nullable=False, server_default=text("'USD'")
    )

    period_start: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    period_end: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    imported_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    imported_by_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    line_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    total_cost_micros: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("0")
    )

    details: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONB, nullable=True)

    lines: Mapped[list["ProviderStatementLine"]] = relationship(
        "ProviderStatementLine",
        back_populates="statement",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<ProviderStatement {self.provider} {self.source_key} "
            f"{self.grain}/{self.attribution} {self.total_cost_micros}µ>"
        )


class ProviderStatementLine(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "provider_statement_lines"

    __table_args__ = (
        CheckConstraint("cost_micros >= 0", name="cost_non_negative"),
        CheckConstraint(
            "quantity IS NULL OR quantity >= 0", name="quantity_non_negative"
        ),
        Index(
            "ix_provider_statement_lines_statement",
            "provider_statement_id",
            "model",
        ),
        Index(
            "ix_provider_statement_lines_org",
            "organization_id",
            postgresql_where=text("organization_id IS NOT NULL"),
        ),
    )

    provider_statement_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("provider_statements.id", ondelete="CASCADE"),
        nullable=False,
    )
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    sku: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    model: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    event_type: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    organization_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="SET NULL"),
        nullable=True,
    )

    occurred_on: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    quantity: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(30, 6), nullable=True
    )
    unit: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    cost_micros: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(
        String(3), nullable=False, server_default=text("'USD'")
    )

    raw: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONB, nullable=True)

    statement: Mapped[ProviderStatement] = relationship(
        "ProviderStatement", back_populates="lines"
    )

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<ProviderStatementLine {self.provider}/{self.model or self.sku} "
            f"{self.quantity} {self.cost_micros}µ>"
        )


class ReconciliationRun(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "reconciliation_runs"

    __table_args__ = (
        CheckConstraint("period_end > period_start", name="period_ordered"),
        CheckConstraint(
            "status IN ('RUNNING', 'COMPLETED', 'FAILED', 'REFUSED')",
            name="status_known",
        ),
        CheckConstraint(
            "attribution IN ('ATTESTED', 'ALLOCATED', 'AGGREGATE')",
            name="attribution_known",
        ),
        CheckConstraint("findings_count >= 0", name="findings_non_negative"),
        Index(
            "ix_reconciliation_runs_period",
            "provider",
            "period_start",
            text("started_at DESC"),
        ),
        Index(
            "ix_reconciliation_runs_alerts",
            text("started_at DESC"),
            postgresql_where=text("alert_raised"),
        ),
    )

    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    period_start: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    period_end: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    provider_statement_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("provider_statements.id", ondelete="RESTRICT"),
        nullable=True,
    )

    grain: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    attribution: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'AGGREGATE'")
    )

    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'RUNNING'")
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    completed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    ledger_cost_micros: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("0")
    )
    statement_cost_micros: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("0")
    )
    drift_micros: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("0")
    )
    drift_bps: Mapped[Decimal] = mapped_column(
        Numeric(12, 4), nullable=False, server_default=text("0")
    )

    findings_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    alert_raised: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )

    details: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONB, nullable=True)

    findings: Mapped[list["ReconciliationFinding"]] = relationship(
        "ReconciliationFinding",
        back_populates="run",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<ReconciliationRun {self.provider} {self.period_start.date()} "
            f"{self.status} drift={self.drift_micros}µ ({self.drift_bps}bps)>"
        )


class ReconciliationFinding(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "reconciliation_findings"

    __table_args__ = (
        CheckConstraint(
            "category IN ('TIMING_BOUNDARY', 'ESTIMATE_DRIFT', 'PRICE_DRIFT', "
            "'UNMETERED_GENERATION', 'OVERMETERED_LEDGER', 'UNEXPLAINED')",
            name="category_known",
        ),
        CheckConstraint(
            "severity IN ('INFO', 'WARNING', 'HIGH', 'CRITICAL')",
            name="severity_known",
        ),
        CheckConstraint(
            "attribution IN ('ATTESTED', 'ALLOCATED', 'AGGREGATE')",
            name="attribution_known",
        ),
        CheckConstraint(
            "organization_id IS NULL OR attribution = 'ATTESTED'",
            name="org_findings_require_attested",
        ),
        CheckConstraint(
            "category <> 'UNMETERED_GENERATION' OR severity = 'CRITICAL'",
            name="unmetered_is_always_critical",
        ),
        Index("ix_reconciliation_findings_run", "reconciliation_run_id", "category"),
        Index(
            "ix_reconciliation_findings_critical",
            text("created_at DESC"),
            postgresql_where=text("severity = 'CRITICAL'"),
        ),
    )

    reconciliation_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("reconciliation_runs.id", ondelete="CASCADE"),
        nullable=False,
    )

    category: Mapped[str] = mapped_column(String(32), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    attribution: Mapped[str] = mapped_column(String(16), nullable=False)
    organization_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="SET NULL"),
        nullable=True,
    )

    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    model: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    event_type: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    ledger_quantity: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(30, 6), nullable=True
    )
    statement_quantity: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(30, 6), nullable=True
    )
    ledger_cost_micros: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("0")
    )
    statement_cost_micros: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("0")
    )

    drift_micros: Mapped[int] = mapped_column(BigInteger, nullable=False)
    drift_bps: Mapped[Decimal] = mapped_column(
        Numeric(12, 4), nullable=False, server_default=text("0")
    )

    explanation: Mapped[str] = mapped_column(Text, nullable=False)
    details: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONB, nullable=True)

    run: Mapped[ReconciliationRun] = relationship(
        "ReconciliationRun", back_populates="findings"
    )

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<ReconciliationFinding {self.category}/{self.severity} "
            f"{self.provider}/{self.model or '*'} {self.drift_micros}µ>"
        )


__all__ = [
    "CATEGORY_ORDER",
    "DRIFT_ALERT_BPS",
    "Attribution",
    "FindingSeverity",
    "ProviderStatement",
    "ProviderStatementLine",
    "ReconciliationCategory",
    "ReconciliationFinding",
    "ReconciliationRun",
    "ReconciliationStatus",
    "StatementGrain",
]
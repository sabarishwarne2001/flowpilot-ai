"""ARCH-35 §6.5 — calibration labels and fitted, versioned calibration models.

VOCABULARY IS READ, NOT DECLARED
================================

Same arrangement as `app/models/assertion.py` and `app/models/radar.py`: the
closed enums live in `app/services/calibration/vocabulary.py`, a module the
pure engines import without the declarative registry. The CHECK bodies here
are built from it, and the migration carries literal copies that
`verify_arch35.py` asserts are equal.

WHY THE ORM CLASS IS `CalibrationModelVersion`
==============================================

`app.services.assertions.calibration.CalibrationModel` is ARCH-33's frozen
dataclass — the in-memory score->probability map every assertion caller
already holds. A mapped class with the same name would make every file that
touches both read ambiguously. A row in `calibration_models` is one VERSION of
a tenant's model for one decision type; the class says so.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any, Optional

from sqlalchemy import (
    CHAR,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, UUIDMixin
from app.services.calibration import vocabulary as vocab

__all__ = ["CalibrationLabel", "CalibrationModelVersion"]


def _quoted(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


class CalibrationLabel(Base, UUIDMixin):
    """One reviewer-confirmed example: the score at decision time, and whether
    the platform was right."""

    __tablename__ = "calibration_labels"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="RESTRICT"),
        nullable=False,
    )
    decision_type: Mapped[str] = mapped_column(String(64), nullable=False)
    raw_score: Mapped[Decimal] = mapped_column(Numeric(8, 7), nullable=False)
    #: Engine correctness — the reviewer agreed with what the platform said.
    #: A confirmed FAIL is CORRECT.
    correct: Mapped[bool] = mapped_column(Boolean, nullable=False)
    source_table: Mapped[str] = mapped_column(String(48), nullable=False)
    source_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    #: The automatic decision was "approve" — including audits that were held
    #: back for review.
    was_auto_approved: Mapped[bool] = mapped_column(Boolean, nullable=False)
    was_audit_sample: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    auto_eligible: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    sample_weight: Mapped[Decimal] = mapped_column(
        Numeric(8, 3), nullable=False, default=Decimal("1"), server_default=text("1")
    )
    observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint("raw_score >= 0 AND raw_score <= 1", name="ck_cl_score_unit"),
        CheckConstraint(
            f"decision_type IN ({_quoted(vocab.DECISION_TYPES)})",
            name="ck_cl_decision_known",
        ),
        CheckConstraint(
            f"source_table IN ({_quoted(vocab.SOURCE_TABLES)})",
            name="ck_cl_source_known",
        ),
        CheckConstraint(
            "NOT was_audit_sample OR was_auto_approved",
            name="ck_cl_audit_was_automatic",
        ),
        CheckConstraint(
            "sample_weight >= 1 AND (was_audit_sample OR sample_weight = 1)",
            name="ck_cl_weight_is_audit_inverse",
        ),
        Index(
            "uq_cl_source", "source_table", "source_id", "decision_type", unique=True
        ),
        Index(
            "ix_cl_org_type_time",
            "organization_id",
            "decision_type",
            text("observed_at DESC"),
        ),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"<CalibrationLabel {self.decision_type} {self.raw_score} "
            f"correct={self.correct} audit={self.was_audit_sample}>"
        )


class CalibrationModelVersion(Base, UUIDMixin):
    """One fitted version of a tenant's model for one decision type."""

    __tablename__ = "calibration_models"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="RESTRICT"),
        nullable=False,
    )
    decision_type: Mapped[str] = mapped_column(String(64), nullable=False)
    method: Mapped[str] = mapped_column(String(16), nullable=False)
    label_count: Mapped[int] = mapped_column(Integer, nullable=False)
    #: ISOTONIC `{"x": [...], "y": [...]}`, PLATT `{"a": ..., "b": ...}`,
    #: PRIOR `{}`. Decimal strings.
    breakpoints: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    ece_before: Mapped[Decimal] = mapped_column(Numeric(7, 6), nullable=False)
    ece_after: Mapped[Decimal] = mapped_column(Numeric(7, 6), nullable=False)
    brier_after: Mapped[Decimal] = mapped_column(Numeric(7, 6), nullable=False)
    #: α — the bound on wrong automatic approvals per document processed.
    target_error_rate: Mapped[Decimal] = mapped_column(Numeric(6, 5), nullable=False)
    #: λ — the calibrated probability an automatic approval needs.
    threshold: Mapped[Decimal] = mapped_column(Numeric(8, 7), nullable=False)
    conformal_bound: Mapped[Decimal] = mapped_column(Numeric(6, 5), nullable=False)
    clopper_pearson_upper: Mapped[Decimal] = mapped_column(
        Numeric(6, 5), nullable=False
    )
    auto_share: Mapped[Decimal] = mapped_column(Numeric(6, 5), nullable=False)
    audit_sample_rate: Mapped[Decimal] = mapped_column(
        Numeric(5, 4),
        nullable=False,
        default=Decimal(vocab.DEFAULT_AUDIT_SAMPLE_RATE),
        server_default=text(vocab.DEFAULT_AUDIT_SAMPLE_RATE),
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    suspended_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    suspended_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    input_digest: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    diagnostics: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    fitted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    last_checked_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        CheckConstraint(
            f"method IN ({_quoted(vocab.METHODS)})", name="ck_cm_method_known"
        ),
        CheckConstraint(
            f"status IN ({_quoted(vocab.STATUSES)})", name="ck_cm_status_known"
        ),
        CheckConstraint(
            f"decision_type IN ({_quoted(vocab.DECISION_TYPES)})",
            name="ck_cm_decision_known",
        ),
        CheckConstraint(
            "status = 'REJECTED' OR ece_after <= ece_before", name="ck_cm_improves"
        ),
        CheckConstraint(
            "target_error_rate > 0 AND target_error_rate <= "
            f"{vocab.TARGET_ERROR_RATE_MAX}",
            name="ck_cm_alpha_bounded",
        ),
        CheckConstraint(
            f"(method = 'ISOTONIC' AND label_count >= {vocab.MIN_LABELS_ISOTONIC}) "
            f"OR (method = 'PLATT' AND label_count >= {vocab.MIN_LABELS_PLATT} "
            f"AND label_count < {vocab.MIN_LABELS_ISOTONIC}) "
            f"OR (method = 'PRIOR' AND label_count < {vocab.MIN_LABELS_PLATT})",
            name="ck_cm_method_needs_labels",
        ),
        CheckConstraint(
            f"(method = 'PRIOR') = (label_count < {vocab.MIN_LABELS_PLATT})",
            name="ck_cm_prior_iff_cold",
        ),
        CheckConstraint("label_count >= 0", name="ck_cm_label_count_non_negative"),
        CheckConstraint(
            "status <> 'SUSPENDED' OR "
            "(suspended_reason IS NOT NULL AND btrim(suspended_reason) <> '' "
            "AND suspended_at IS NOT NULL)",
            name="ck_cm_suspension_has_reason",
        ),
        CheckConstraint(
            "auto_share = 0 OR "
            "(conformal_bound <= target_error_rate AND threshold < 1 "
            "AND method <> 'PRIOR')",
            name="ck_cm_promise_backed",
        ),
        CheckConstraint(
            f"audit_sample_rate >= {vocab.AUDIT_SAMPLE_RATE_MIN} "
            f"AND audit_sample_rate <= {vocab.AUDIT_SAMPLE_RATE_MAX}",
            name="ck_cm_audit_rate_bounded",
        ),
        CheckConstraint(
            "ece_before >= 0 AND ece_before <= 1 "
            "AND ece_after >= 0 AND ece_after <= 1 "
            "AND brier_after >= 0 AND brier_after <= 1 "
            "AND threshold >= 0 AND threshold <= 1 "
            "AND conformal_bound >= 0 AND conformal_bound <= 1 "
            "AND clopper_pearson_upper >= 0 AND clopper_pearson_upper <= 1 "
            "AND auto_share >= 0 AND auto_share <= 1",
            name="ck_cm_unit_intervals",
        ),
        CheckConstraint(
            "jsonb_typeof(breakpoints) = 'object' "
            "AND jsonb_typeof(diagnostics) = 'object'",
            name="ck_cm_json_objects",
        ),
        CheckConstraint(
            "input_digest ~ '^[0-9a-f]{64}$'", name="ck_cm_digest_shape"
        ),
        Index(
            "uq_cm_active",
            "organization_id",
            "decision_type",
            unique=True,
            postgresql_where=text(f"status IN ({_quoted(vocab.LIVE_STATUSES)})"),
        ),
        Index(
            "ix_cm_org_type_fitted",
            "organization_id",
            "decision_type",
            text("fitted_at DESC"),
        ),
    )

    @property
    def is_live(self) -> bool:
        return self.status in vocab.LIVE_STATUSES

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"<CalibrationModelVersion {self.decision_type} {self.method} "
            f"{self.status} n={self.label_count} λ={self.threshold}>"
        )

"""ARCH-35 Step 1 — calibration labels, calibration models, and the ARCH-33 FK (EXPAND)

Revision ID: arch35_step1_calibration
Revises: arch34_step1_radar
Create Date: 2026-09-16

PURE EXPAND
===========

Two new tables and one new foreign key. Nothing existing is narrowed or
dropped. The foreign key is added to a column ARCH-33 created nullable and
deliberately unconstrained, exactly so this phase could attach it without
touching ARCH-33's table definition; every value ARCH-33 ever wrote into it is
NULL (its interim estimator never persisted a model), so `ADD CONSTRAINT`
validates against nothing and cannot fail on existing rows.

THIRD-PARTY LICENSES INTRODUCED BY THIS PHASE
=============================================

None. The estimators use scikit-learn (BSD-3-Clause), SciPy (BSD-3-Clause) and
NumPy (BSD-3-Clause), all already in `requirements.txt`. Recorded here because
a migration header is the one document in a repository nobody deletes.

THE CONSTRAINTS THAT PUT THE STATISTICS IN THE DATABASE
=======================================================

  ck_cm_method_needs_labels   ISOTONIC needs >= 200 labels, PLATT 50-199,
                              PRIOR < 50. An isotonic model on 80 labels
                              cannot become the model in force.
  ck_cm_prior_iff_cold        PRIOR if and only if fewer than 50 labels.
  ck_cm_improves              a fit that made ECE worse is REJECTED, full stop.
  ck_cm_promise_backed        a model may approve anything automatically only
                              if its conformal bound is within α.
  ck_cm_audit_rate_bounded    audit sampling can be tuned, never switched off.
  ck_cm_suspension_has_reason a paused decision always says why, and when.

`verify_arch35.py --db` drives each of these with an INSERT inside a
rolled-back transaction and requires Postgres to refuse it.

COLUMNS BEYOND THE ROADMAP'S DDL, AND WHY
=========================================

calibration_labels.sample_weight
    Audit labels stand for 1/r automatic passes. Counting them once each makes
    the conformal bound optimistic by the audit rate's inverse.
calibration_labels.auto_eligible
    An assertion FAIL is a label but can never be approved automatically; it
    counts toward n and never toward the loss.
calibration_models.input_digest
    A nightly refit over unchanged labels writes nothing.
calibration_models.diagnostics
    The reliability bins, the error-versus-coverage curve and the PSI
    reference distribution the model was fitted against. Immutable with the
    model, because the console must show what the promise was computed on.
calibration_models.last_checked_at
    "Stale" means unmonitored, not old: a model the nightly monitor keeps
    confirming stays in force without being refitted.
calibration_models.suspended_at
    Resuming requires reviews recorded AFTER the pause.

NAMES THAT AVOID A KNOWN COLLISION
==================================

`verify_arch33.py --db` locates ARCH-33's node CHECK with
`conname LIKE '%type_known%'` and takes the first row. No constraint here has a
name matching that pattern (or `%branch_known%`), which is why the decision
type CHECKs are `ck_cl_decision_known` / `ck_cm_decision_known`.

WHY THE VOCABULARY LISTS ARE DECLARED HERE AND ASSERTED, NOT IMPORTED
=====================================================================

Same reason as every migration since ARCH-31: a migration that imports
application code stops running the day that import path moves.
`verify_arch35.py` asserts these copies equal
`app/services/calibration/vocabulary.py`.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "arch35_step1_calibration"
down_revision = "arch34_step1_radar"
branch_labels = None
depends_on = None


# --- copies of app/services/calibration/vocabulary.py, gated for equality ---

METHODS: tuple[str, ...] = ("ISOTONIC", "PLATT", "PRIOR")
STATUSES: tuple[str, ...] = ("ACTIVE", "SUPERSEDED", "SUSPENDED", "REJECTED")
LIVE_STATUSES: tuple[str, ...] = ("ACTIVE", "SUSPENDED")
DECISION_TYPES: tuple[str, ...] = (
    "verification.document",
    "verification.field",
    "reconciliation.case",
    "anomaly.finding",
    "assertion.duration_bound",
    "assertion.notice_period",
    "assertion.money_multiple_bound",
    "assertion.money_bound",
    "assertion.enumerated",
    "assertion.presence",
    "assertion.absence",
    "assertion.llm",
)
SOURCE_TABLES: tuple[str, ...] = (
    "document_verifications",
    "document_verification_fields",
    "assertion_evaluations",
    "procurement_cases",
    "anomaly_findings",
)
MIN_LABELS_PLATT: int = 50
MIN_LABELS_ISOTONIC: int = 200
TARGET_ERROR_RATE_MAX: str = "0.2"
DEFAULT_AUDIT_SAMPLE_RATE: str = "0.02"
AUDIT_SAMPLE_RATE_MIN: str = "0.01"
AUDIT_SAMPLE_RATE_MAX: str = "0.25"


def _quoted(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def upgrade() -> None:
    # -----------------------------------------------------------------
    # 1. calibration_labels — one reviewer-confirmed example
    # -----------------------------------------------------------------
    op.create_table(
        "calibration_labels",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "organization_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("decision_type", sa.String(64), nullable=False),
        sa.Column("raw_score", sa.Numeric(8, 7), nullable=False),
        sa.Column("correct", sa.Boolean(), nullable=False),
        sa.Column("source_table", sa.String(48), nullable=False),
        sa.Column("source_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("was_auto_approved", sa.Boolean(), nullable=False),
        sa.Column(
            "was_audit_sample",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "auto_eligible",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column(
            "sample_weight",
            sa.Numeric(8, 3),
            nullable=False,
            server_default=sa.text("1"),
        ),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "raw_score >= 0 AND raw_score <= 1", name="ck_cl_score_unit"
        ),
        sa.CheckConstraint(
            f"decision_type IN ({_quoted(DECISION_TYPES)})",
            name="ck_cl_decision_known",
        ),
        sa.CheckConstraint(
            f"source_table IN ({_quoted(SOURCE_TABLES)})",
            name="ck_cl_source_known",
        ),
        # An audit sample is, by definition, something the platform would
        # have approved automatically.
        sa.CheckConstraint(
            "NOT was_audit_sample OR was_auto_approved",
            name="ck_cl_audit_was_automatic",
        ),
        # Weight above 1 only for an audit: nothing else is sampled.
        sa.CheckConstraint(
            "sample_weight >= 1 AND (was_audit_sample OR sample_weight = 1)",
            name="ck_cl_weight_is_audit_inverse",
        ),
    )
    op.create_index(
        "uq_cl_source",
        "calibration_labels",
        ["source_table", "source_id", "decision_type"],
        unique=True,
    )
    op.create_index(
        "ix_cl_org_type_time",
        "calibration_labels",
        ["organization_id", "decision_type", sa.text("observed_at DESC")],
    )

    # -----------------------------------------------------------------
    # 2. calibration_models — a fitted, versioned model
    # -----------------------------------------------------------------
    op.create_table(
        "calibration_models",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "organization_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("decision_type", sa.String(64), nullable=False),
        sa.Column("method", sa.String(16), nullable=False),
        sa.Column("label_count", sa.Integer(), nullable=False),
        sa.Column("breakpoints", postgresql.JSONB(), nullable=False),
        sa.Column("ece_before", sa.Numeric(7, 6), nullable=False),
        sa.Column("ece_after", sa.Numeric(7, 6), nullable=False),
        sa.Column("brier_after", sa.Numeric(7, 6), nullable=False),
        sa.Column("target_error_rate", sa.Numeric(6, 5), nullable=False),
        sa.Column("threshold", sa.Numeric(8, 7), nullable=False),
        sa.Column("conformal_bound", sa.Numeric(6, 5), nullable=False),
        sa.Column("clopper_pearson_upper", sa.Numeric(6, 5), nullable=False),
        sa.Column("auto_share", sa.Numeric(6, 5), nullable=False),
        sa.Column(
            "audit_sample_rate",
            sa.Numeric(5, 4),
            nullable=False,
            server_default=sa.text(DEFAULT_AUDIT_SAMPLE_RATE),
        ),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("suspended_reason", sa.Text(), nullable=True),
        sa.Column("suspended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("input_digest", sa.CHAR(64), nullable=False),
        sa.Column(
            "diagnostics",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "fitted_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("last_checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            f"method IN ({_quoted(METHODS)})", name="ck_cm_method_known"
        ),
        sa.CheckConstraint(
            f"status IN ({_quoted(STATUSES)})", name="ck_cm_status_known"
        ),
        sa.CheckConstraint(
            f"decision_type IN ({_quoted(DECISION_TYPES)})",
            name="ck_cm_decision_known",
        ),
        sa.CheckConstraint(
            "status = 'REJECTED' OR ece_after <= ece_before",
            name="ck_cm_improves",
        ),
        sa.CheckConstraint(
            f"target_error_rate > 0 AND target_error_rate <= {TARGET_ERROR_RATE_MAX}",
            name="ck_cm_alpha_bounded",
        ),
        sa.CheckConstraint(
            f"(method = 'ISOTONIC' AND label_count >= {MIN_LABELS_ISOTONIC}) "
            f"OR (method = 'PLATT' AND label_count >= {MIN_LABELS_PLATT} "
            f"AND label_count < {MIN_LABELS_ISOTONIC}) "
            f"OR (method = 'PRIOR' AND label_count < {MIN_LABELS_PLATT})",
            name="ck_cm_method_needs_labels",
        ),
        sa.CheckConstraint(
            f"(method = 'PRIOR') = (label_count < {MIN_LABELS_PLATT})",
            name="ck_cm_prior_iff_cold",
        ),
        sa.CheckConstraint(
            "label_count >= 0", name="ck_cm_label_count_non_negative"
        ),
        sa.CheckConstraint(
            "status <> 'SUSPENDED' OR "
            "(suspended_reason IS NOT NULL AND btrim(suspended_reason) <> '' "
            "AND suspended_at IS NOT NULL)",
            name="ck_cm_suspension_has_reason",
        ),
        sa.CheckConstraint(
            "auto_share = 0 OR "
            "(conformal_bound <= target_error_rate AND threshold < 1 "
            "AND method <> 'PRIOR')",
            name="ck_cm_promise_backed",
        ),
        sa.CheckConstraint(
            f"audit_sample_rate >= {AUDIT_SAMPLE_RATE_MIN} "
            f"AND audit_sample_rate <= {AUDIT_SAMPLE_RATE_MAX}",
            name="ck_cm_audit_rate_bounded",
        ),
        sa.CheckConstraint(
            "ece_before >= 0 AND ece_before <= 1 "
            "AND ece_after >= 0 AND ece_after <= 1 "
            "AND brier_after >= 0 AND brier_after <= 1 "
            "AND threshold >= 0 AND threshold <= 1 "
            "AND conformal_bound >= 0 AND conformal_bound <= 1 "
            "AND clopper_pearson_upper >= 0 AND clopper_pearson_upper <= 1 "
            "AND auto_share >= 0 AND auto_share <= 1",
            name="ck_cm_unit_intervals",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(breakpoints) = 'object' "
            "AND jsonb_typeof(diagnostics) = 'object'",
            name="ck_cm_json_objects",
        ),
        sa.CheckConstraint(
            "input_digest ~ '^[0-9a-f]{64}$'", name="ck_cm_digest_shape"
        ),
    )
    op.create_index(
        "uq_cm_active",
        "calibration_models",
        ["organization_id", "decision_type"],
        unique=True,
        postgresql_where=sa.text(f"status IN ({_quoted(LIVE_STATUSES)})"),
    )
    op.create_index(
        "ix_cm_org_type_fitted",
        "calibration_models",
        ["organization_id", "decision_type", sa.text("fitted_at DESC")],
    )

    # -----------------------------------------------------------------
    # 3. ARCH-33 seam closure
    # -----------------------------------------------------------------
    op.create_foreign_key(
        "fk_ae_calibration_model",
        "assertion_evaluations",
        "calibration_models",
        ["calibration_model_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_ae_calibration_model",
        "assertion_evaluations",
        ["calibration_model_id"],
        postgresql_where=sa.text("calibration_model_id IS NOT NULL"),
    )


def downgrade() -> None:
    """Drop the FK, then the two tables.

    Honest in the same way ARCH-34's downgrade is: nothing in these tables
    predates this phase. The foreign key is removed first so that
    `assertion_evaluations` returns to exactly the shape ARCH-33 shipped — a
    bare nullable uuid — and ARCH-33's rows keep whatever ids they hold.
    """
    op.drop_index("ix_ae_calibration_model", table_name="assertion_evaluations")
    op.drop_constraint(
        "fk_ae_calibration_model", "assertion_evaluations", type_="foreignkey"
    )
    op.drop_index("ix_cm_org_type_fitted", table_name="calibration_models")
    op.drop_index("uq_cm_active", table_name="calibration_models")
    op.drop_table("calibration_models")
    op.drop_index("ix_cl_org_type_time", table_name="calibration_labels")
    op.drop_index("uq_cl_source", table_name="calibration_labels")
    op.drop_table("calibration_labels")

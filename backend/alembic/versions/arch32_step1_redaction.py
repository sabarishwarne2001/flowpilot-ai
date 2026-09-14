"""ARCH-32 Step 1 — redaction jobs and regions (EXPAND)

Revision ID: arch32_step1_redaction
Revises: arch31_step1_procurement_matching
Create Date: 2026-09-14

EXPAND-SHAPED
=============

Two new tables. Nothing existing is altered, so this is safe to run before the
code that reads it ships and safe to leave in place if the phase is reverted.

THIRD-PARTY LICENSES INTRODUCED BY THIS PHASE
=============================================

Recorded here because a migration header is the one document in a repository
that nobody deletes, and "which copyleft did we take on, and when" is a
question that gets asked years later by somebody doing diligence.

    pikepdf     MPL-2.0
                Builds the output document. File-level copyleft: modifications
                to pikepdf's own source would have to be published; linking it
                into a closed-source service does not.

    pypdfium2   BSD-3-Clause (bindings) wrapping PDFium, BSD-3-Clause
                Rendering and text extraction. Permissive; attribution only.

    numpy       BSD-3-Clause. Already a dependency (ARCH-11 embeddings).

PyMuPDF is deliberately NOT used anywhere in ARCH-32. It is the obvious
library for this job and its license is AGPL-3.0, which would require
publishing the source of a network-accessible service that links it. A
transitive import of it is a licensing incident, not a bug.

THE CONSTRAINT THAT CARRIES THE PRODUCT
=======================================

`ck_rj_completed_is_sealed`. A row cannot say COMPLETED unless it holds an
output file, an output hash, a manifest, `leak_check_passed IS TRUE` and an
approval timestamp.

This is at the database and not in the service on purpose. Every service-level
expression of the same rule survives only as long as nobody reorders the
statements around it: a status set before the commit that writes the hash, an
exception swallowed by the worker's retry wrapper, a process killed between
two flushes. None of those can produce a COMPLETED row here. The phase's
entire commercial claim is "content that was redacted does not exist in this
file", and the row asserting a job finished is the only place that claim is
recorded — so it is the place that must be impossible to write dishonestly.

`verify_arch32.py --db` drives an UPDATE setting COMPLETED with
`leak_check_passed = false` inside a rolled-back transaction and requires
Postgres to refuse it.

WHY REGIONS CARRY `organization_id` AND `workspace_id`
======================================================

ARCH-02. A child table scoped only through its parent is a child table
somebody eventually queries without the join, and the first time that happens
in a redaction context the query returns another tenant's rectangles. Both
columns are NOT NULL and both are indexed.

WHY `token_digest` IS `char(64)` AND NULLABLE
=============================================

It holds an HMAC-SHA256 hex digest of the matched text, keyed through the
ARCH-07 key lifecycle — never the text. NULL is correct and expected for
`detector = 'manual'`: a hand-drawn rectangle has no matched token, and
storing an HMAC of the empty string would put a constant in the column that
reads like evidence.

WHY THERE IS NO `redaction_tokens` TABLE
========================================

The obvious design keeps the matched text somewhere so the leak check can run
later. It is the wrong design: it recreates, in one queryable table, exactly
the corpus of identifiers that every document in this phase was redacted to
protect, and it survives the job that needed it. The apply worker re-derives
detections from the source in memory, uses them for the leak check, and
returns; nothing is written. See `app/services/redaction/leakcheck.py`.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "arch32_step1_redaction"
down_revision = "arch31_step1_procurement_matching"
branch_labels = None
depends_on = None


# These four lists are asserted equal to
# `app/services/redaction/vocabulary.py` by `verify_arch32.py`. A value added
# in one place and not the other fails a gate rather than a production insert.
JOB_STATUSES: tuple[str, ...] = (
    "DETECTING",
    "REVIEW",
    "APPLYING",
    "COMPLETED",
    "FAILED",
    "CANCELLED",
)

GEOMETRY_PRECISIONS: tuple[str, ...] = ("GLYPH", "BLOCK", "MANUAL")

MIN_RENDER_DPI = 150
MAX_RENDER_DPI = 600
DEFAULT_RENDER_DPI = 300


def _quoted(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def upgrade() -> None:
    op.create_table(
        "redaction_jobs",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "organization_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "workspace_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "work_item_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("work_items.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "source_file_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("uploaded_files.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("source_sha256", sa.CHAR(64), nullable=False),
        sa.Column(
            "status",
            sa.String(16),
            nullable=False,
            server_default=sa.text("'DETECTING'"),
        ),
        sa.Column("profile_key", sa.String(64), nullable=False),
        sa.Column(
            "render_dpi",
            sa.SmallInteger(),
            nullable=False,
            server_default=sa.text(str(DEFAULT_RENDER_DPI)),
        ),
        sa.Column(
            "restore_text_layer",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column(
            "output_file_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("uploaded_files.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("output_sha256", sa.CHAR(64), nullable=True),
        sa.Column(
            "manifest_file_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("uploaded_files.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("leak_check_passed", sa.Boolean(), nullable=True),
        sa.Column(
            "leak_check_detail",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column("input_digest", sa.CHAR(64), nullable=True),
        sa.Column("page_count", sa.Integer(), nullable=True),
        sa.Column(
            "approved_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failure_reason", sa.Text(), nullable=True),
        sa.Column(
            "created_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            f"status IN ({_quoted(JOB_STATUSES)})",
            name="ck_rj_status_known",
        ),
        sa.CheckConstraint(
            f"render_dpi BETWEEN {MIN_RENDER_DPI} AND {MAX_RENDER_DPI}",
            name="ck_rj_dpi_bounded",
        ),
        sa.CheckConstraint(
            "status <> 'COMPLETED' OR ("
            "output_file_id IS NOT NULL AND output_sha256 IS NOT NULL "
            "AND manifest_file_id IS NOT NULL AND leak_check_passed IS TRUE "
            "AND approved_at IS NOT NULL)",
            name="ck_rj_completed_is_sealed",
        ),
        sa.CheckConstraint(
            "status <> 'FAILED' OR failure_reason IS NOT NULL",
            name="ck_rj_failed_has_reason",
        ),
    )

    op.create_index(
        "ix_redaction_jobs_workspace_status",
        "redaction_jobs",
        ["workspace_id", "status"],
    )
    op.create_index("ix_redaction_jobs_work_item", "redaction_jobs", ["work_item_id"])
    op.create_index(
        "ix_redaction_jobs_organization", "redaction_jobs", ["organization_id"]
    )
    op.create_index(
        "ix_redaction_jobs_live",
        "redaction_jobs",
        ["workspace_id", "created_at"],
        postgresql_where=sa.text("status IN ('DETECTING', 'REVIEW', 'APPLYING')"),
    )

    op.create_table(
        "redaction_regions",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "job_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("redaction_jobs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "organization_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "workspace_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("page_number", sa.Integer(), nullable=False),
        sa.Column("x0", sa.Numeric(10, 4), nullable=False),
        sa.Column("y0", sa.Numeric(10, 4), nullable=False),
        sa.Column("x1", sa.Numeric(10, 4), nullable=False),
        sa.Column("y1", sa.Numeric(10, 4), nullable=False),
        sa.Column("detector", sa.String(32), nullable=False),
        sa.Column("confidence", sa.Numeric(5, 4), nullable=False),
        sa.Column("geometry_precision", sa.String(8), nullable=False),
        sa.Column(
            "enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")
        ),
        sa.Column("token_digest", sa.CHAR(64), nullable=True),
        sa.Column(
            "created_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "toggled_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint("x1 > x0 AND y1 > y0", name="ck_rr_box_ordered"),
        sa.CheckConstraint("page_number >= 1", name="ck_rr_page_positive"),
        sa.CheckConstraint(
            f"geometry_precision IN ({_quoted(GEOMETRY_PRECISIONS)})",
            name="ck_rr_precision_known",
        ),
        sa.CheckConstraint(
            "detector <> 'manual' OR created_by_user_id IS NOT NULL",
            name="ck_rr_manual_has_author",
        ),
        sa.CheckConstraint(
            "confidence >= 0 AND confidence <= 1",
            name="ck_rr_confidence_unit_interval",
        ),
    )

    op.create_index("ix_rr_job_page", "redaction_regions", ["job_id", "page_number"])
    op.create_index(
        "ix_redaction_regions_organization", "redaction_regions", ["organization_id"]
    )
    op.create_index(
        "ix_redaction_regions_workspace", "redaction_regions", ["workspace_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_redaction_regions_workspace", table_name="redaction_regions")
    op.drop_index("ix_redaction_regions_organization", table_name="redaction_regions")
    op.drop_index("ix_rr_job_page", table_name="redaction_regions")
    op.drop_table("redaction_regions")

    op.drop_index("ix_redaction_jobs_live", table_name="redaction_jobs")
    op.drop_index("ix_redaction_jobs_organization", table_name="redaction_jobs")
    op.drop_index("ix_redaction_jobs_work_item", table_name="redaction_jobs")
    op.drop_index("ix_redaction_jobs_workspace_status", table_name="redaction_jobs")
    op.drop_table("redaction_jobs")
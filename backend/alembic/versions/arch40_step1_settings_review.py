"""ARCH-40 Step 1 — EXPAND: settings contracts, email override, review hub.

Revision ID: arch40_step1_settings_review
Revises: arch40_step0_review_vocabulary

EXPAND ONLY. Nothing is dropped here. The three dead `ai_settings` columns are
relaxed to nullable with a server default so that code which has stopped
writing them still inserts, and code which has not yet been deployed still
reads them. `arch40_step3_contract_ai_settings` drops them, one release later.

WHY THE DEAD COLUMNS ARE RELAXED RATHER THAN LEFT ALONE
=======================================================

`system_prompt_version`, `prompt_version` and `enable_token_tracking` are all
NOT NULL with no server default. ARCH-40 removes them from the SQLAlchemy
model, which means an INSERT from the new code omits them — and would fail on
the NOT NULL. Relaxing them here is what makes the expand/contract split
possible at all: between step 1 and step 3 the column exists, accepts NULL,
and nothing reads it.

WHAT THE DATABASE NOW REFUSES ON ai_settings
=============================================

Every surviving column gains a CHECK naming its real contract. Before ARCH-40
this table had none: `temperature = 9000` was a legal row, refused only by a
zod schema in the browser.

  ck_ai_settings_temperature_range          0 <= temperature <= 2
  ck_ai_settings_top_p_range                0 <= top_p <= 1
  ck_ai_settings_frequency_penalty_range    -2 <= frequency_penalty <= 2
  ck_ai_settings_presence_penalty_range     -2 <= presence_penalty <= 2
  ck_ai_settings_max_output_tokens_range    1 <= max_output_tokens <= 32768
  ck_ai_settings_model_present              the model name is not blank

Each is added with raw `ALTER TABLE ... ADD CONSTRAINT <exact name>`. Both
`op.create_check_constraint` and `sa.CheckConstraint` inside `op.create_table`
pass through the metadata naming convention and emit `ck_<table>_ck_<table>_…`,
which a gate looking for the documented name cannot find. ARCH-31, 34, 37 and
38 all take the raw route for the same reason.

TENANT ISOLATION WITHOUT A TRIGGER
==================================

`review_assignments` carries a composite foreign key onto
`workspace_members (user_id, workspace_id)` with ON DELETE CASCADE. That single
constraint answers two questions at once:

  * an assignee must be a member of the workspace the item lives in — a CHECK
    could never express this, being single-row;
  * an assignee who loses workspace access does not remain assigned. The
    membership row goes, the assignment goes with it, in the same statement.

It is a sweep, and the database performs it. Not a query-time filter: a
filtered-out assignment is still a row that says a person owns an item they
cannot open, and every report reading the table directly would disagree with
the hub.

`workspace_email_overrides` gets the same treatment against
`workspaces (id, organization_id)`, so an override cannot name a workspace
belonging to another organization.

THE VIEW IS A PROJECTION, NOT A STORE
=====================================

`review_queue_items` unions the three existing tables. It holds nothing. A
fourth store would be a write path that drifts from its sources, and the first
time it drifted a reviewer would resolve an item that no longer exists.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "arch40_step1_settings_review"
down_revision = "arch40_step0_review_vocabulary"
branch_labels = None
depends_on = None


#: ARCH40-S1:dead-ai-columns. Named once, used by step 1, 2 and 3 alike.
DEAD_AI_SETTINGS_COLUMNS: tuple[tuple[str, str, str], ...] = (
    ("system_prompt_version", "VARCHAR(50)", "'v1'"),
    ("prompt_version", "VARCHAR(50)", "'v1'"),
    ("enable_token_tracking", "BOOLEAN", "true"),
)

#: ARCH40-S1:ai-settings-checks. Every surviving column states its contract.
AI_SETTINGS_CHECKS: tuple[tuple[str, str], ...] = (
    ("ck_ai_settings_temperature_range", "temperature >= 0 AND temperature <= 2"),
    ("ck_ai_settings_top_p_range", "top_p >= 0 AND top_p <= 1"),
    (
        "ck_ai_settings_frequency_penalty_range",
        "frequency_penalty >= -2 AND frequency_penalty <= 2",
    ),
    (
        "ck_ai_settings_presence_penalty_range",
        "presence_penalty >= -2 AND presence_penalty <= 2",
    ),
    (
        "ck_ai_settings_max_output_tokens_range",
        "max_output_tokens >= 1 AND max_output_tokens <= 32768",
    ),
    ("ck_ai_settings_model_present", "length(btrim(model)) > 0"),
)

REVIEW_KINDS: tuple[str, ...] = ("EXTRACTION", "ASSERTION", "ANOMALY")

#: ARCH40-S1:review-view. The single read model behind GET .../review.
#:
#: severity_rank exists so "sort by severity then age" is one index-friendly
#: ORDER BY rather than a CASE repeated in every caller. 1 is the most urgent,
#: so ORDER BY severity_rank, created_at puts the oldest high-severity item
#: first — which is the order the queue header promises.
REVIEW_QUEUE_VIEW = """
CREATE VIEW review_queue_items AS
    SELECT
        'EXTRACTION'::varchar(16)                       AS kind,
        dv.id                                           AS item_id,
        dv.organization_id                              AS organization_id,
        dv.workspace_id                                 AS workspace_id,
        dv.work_item_id                                 AS work_item_id,
        CASE
            WHEN dv.status = 'DISAGREED'
                THEN 'Extracted fields disagree'
            WHEN coalesce((dv.details ->> 'review_all_fields')::boolean, false)
                THEN 'Audit sample: confirm every field'
            ELSE 'Extraction awaiting review'
        END::text                                       AS headline,
        CASE
            WHEN dv.status = 'DISAGREED' THEN 'HIGH'
            WHEN coalesce((dv.details ->> 'review_all_fields')::boolean, false)
                THEN 'LOW'
            ELSE 'MEDIUM'
        END::varchar(8)                                 AS severity,
        CASE
            WHEN dv.status = 'DISAGREED' THEN 2
            WHEN coalesce((dv.details ->> 'review_all_fields')::boolean, false)
                THEN 4
            ELSE 3
        END::integer                                    AS severity_rank,
        dv.confidence                                   AS confidence,
        dv.created_at                                   AS created_at,
        CASE WHEN dv.status = 'REVIEWED' THEN 'RESOLVED' ELSE 'OPEN' END::varchar(8)
                                                        AS status,
        dv.reviewed_at                                  AS resolved_at,
        dv.reviewed_by_user_id                          AS resolved_by_user_id
    FROM document_verifications dv
    WHERE dv.status IN ('PENDING', 'DISAGREED', 'REVIEWED')

    UNION ALL

    SELECT
        'ASSERTION'::varchar(16)                        AS kind,
        ae.id                                           AS item_id,
        ae.organization_id                              AS organization_id,
        ae.workspace_id                                 AS workspace_id,
        ae.work_item_id                                 AS work_item_id,
        ad.sentence::text                               AS headline,
        CASE
            WHEN ae.verdict = 'FAIL' THEN 'HIGH'
            WHEN ae.calibrated_probability IS NOT NULL
                 AND ae.calibrated_probability < 0.60 THEN 'HIGH'
            ELSE 'MEDIUM'
        END::varchar(8)                                 AS severity,
        CASE
            WHEN ae.verdict = 'FAIL' THEN 2
            WHEN ae.calibrated_probability IS NOT NULL
                 AND ae.calibrated_probability < 0.60 THEN 2
            ELSE 3
        END::integer                                    AS severity_rank,
        ae.calibrated_probability                       AS confidence,
        ae.created_at                                   AS created_at,
        CASE WHEN ae.reviewer_verdict IS NULL THEN 'OPEN' ELSE 'RESOLVED' END::varchar(8)
                                                        AS status,
        ae.reviewed_at                                  AS resolved_at,
        NULL::uuid                                      AS resolved_by_user_id
    FROM assertion_evaluations ae
    JOIN assertion_definitions ad ON ad.id = ae.definition_id
    WHERE ae.routed_to = 'TRIAGE'

    UNION ALL

    SELECT
        'ANOMALY'::varchar(16)                          AS kind,
        af.id                                           AS item_id,
        af.organization_id                              AS organization_id,
        af.workspace_id                                 AS workspace_id,
        af.subject_work_item_id                         AS work_item_id,
        af.headline::text                               AS headline,
        af.severity::varchar(8)                         AS severity,
        CASE af.severity
            WHEN 'HIGH' THEN 2
            WHEN 'MEDIUM' THEN 3
            ELSE 4
        END::integer                                    AS severity_rank,
        af.score                                        AS confidence,
        af.created_at                                   AS created_at,
        CASE WHEN af.status = 'OPEN' THEN 'OPEN' ELSE 'RESOLVED' END::varchar(8)
                                                        AS status,
        af.resolved_at                                  AS resolved_at,
        af.resolved_by_user_id                          AS resolved_by_user_id
    FROM anomaly_findings af
"""


def _sql_list(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def upgrade() -> None:
    # ------------------------------------------------------------------
    # ai_settings — relax the dead columns, constrain the live ones
    # ------------------------------------------------------------------
    for name, _type, default in DEAD_AI_SETTINGS_COLUMNS:
        op.execute(f"ALTER TABLE ai_settings ALTER COLUMN {name} DROP NOT NULL")
        op.execute(f"ALTER TABLE ai_settings ALTER COLUMN {name} SET DEFAULT {default}")

    for name, expression in AI_SETTINGS_CHECKS:
        op.execute(f"ALTER TABLE ai_settings DROP CONSTRAINT IF EXISTS {name}")
        op.execute(
            f"ALTER TABLE ai_settings ADD CONSTRAINT {name} CHECK ({expression})"
        )

    # ------------------------------------------------------------------
    # workspaces — the composite key the email override points at
    # ------------------------------------------------------------------
    op.execute(
        "ALTER TABLE workspaces DROP CONSTRAINT IF EXISTS "
        "uq_workspaces_id_organization_id"
    )
    op.execute(
        "ALTER TABLE workspaces ADD CONSTRAINT uq_workspaces_id_organization_id "
        "UNIQUE (id, organization_id)"
    )

    # ------------------------------------------------------------------
    # workspace_email_overrides
    # ------------------------------------------------------------------
    op.create_table(
        "workspace_email_overrides",
        sa.Column(
            "workspace_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            primary_key=True,
            nullable=False,
        ),
        sa.Column(
            "organization_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "is_enabled", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        sa.Column("smtp_host", sa.String(255), nullable=True),
        sa.Column("smtp_port", sa.Integer(), nullable=True),
        sa.Column("smtp_username", sa.String(255), nullable=True),
        sa.Column("smtp_password_encrypted", sa.String(512), nullable=True),
        sa.Column("encryption", sa.String(8), nullable=False, server_default="TLS"),
        sa.Column("sender_name", sa.String(100), nullable=True),
        sa.Column("from_address", sa.String(320), nullable=True),
        sa.Column("reply_to_address", sa.String(320), nullable=True),
        sa.Column(
            "updated_by_user_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    # The composite FK. `workspace_id` alone would let an override name a
    # workspace in another organization, and the resolver reads
    # organization_id from the row.
    op.execute(
        "ALTER TABLE workspace_email_overrides "
        "ADD CONSTRAINT fk_workspace_email_overrides_workspace_org "
        "FOREIGN KEY (workspace_id, organization_id) "
        "REFERENCES workspaces (id, organization_id) ON DELETE CASCADE"
    )
    op.execute(
        "ALTER TABLE workspace_email_overrides "
        "ADD CONSTRAINT ck_workspace_email_overrides_port_range "
        "CHECK (smtp_port IS NULL OR (smtp_port BETWEEN 1 AND 65535))"
    )
    op.execute(
        "ALTER TABLE workspace_email_overrides "
        "ADD CONSTRAINT ck_workspace_email_overrides_encryption_known "
        "CHECK (encryption IN ('NONE', 'TLS', 'SSL'))"
    )
    # The conditional invariant, in the schema rather than only in a service:
    # an override that is ENABLED is complete enough to send.
    op.execute(
        "ALTER TABLE workspace_email_overrides "
        "ADD CONSTRAINT ck_workspace_email_overrides_enabled_is_complete "
        "CHECK (is_enabled = false OR ("
        "smtp_host IS NOT NULL AND smtp_port IS NOT NULL AND "
        "smtp_username IS NOT NULL AND smtp_password_encrypted IS NOT NULL AND "
        "sender_name IS NOT NULL))"
    )
    op.execute(
        "ALTER TABLE workspace_email_overrides "
        "ADD CONSTRAINT ck_workspace_email_overrides_from_address_shape "
        "CHECK (from_address IS NULL OR from_address ~ '^[^@[:space:]]+@[^@[:space:]]+[.][^@[:space:]]+$')"
    )
    op.execute(
        "ALTER TABLE workspace_email_overrides "
        "ADD CONSTRAINT ck_workspace_email_overrides_reply_to_shape "
        "CHECK (reply_to_address IS NULL OR reply_to_address ~ '^[^@[:space:]]+@[^@[:space:]]+[.][^@[:space:]]+$')"
    )
    op.create_index(
        "ix_workspace_email_overrides_organization_id",
        "workspace_email_overrides",
        ["organization_id"],
    )

    # ------------------------------------------------------------------
    # review_assignments
    # ------------------------------------------------------------------
    op.create_table(
        "review_assignments",
        sa.Column(
            "id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column(
            "item_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=False
        ),
        sa.Column(
            "workspace_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "assignee_user_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "assigned_by_user_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "assigned_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.execute(
        "ALTER TABLE review_assignments ADD CONSTRAINT ck_review_assignments_kind_known "
        f"CHECK (kind IN ({_sql_list(REVIEW_KINDS)}))"
    )
    op.execute(
        "ALTER TABLE review_assignments "
        "ADD CONSTRAINT uq_review_assignments_kind_item UNIQUE (kind, item_id)"
    )
    # The sweep, as a constraint. Losing workspace access deletes the
    # membership row, and this cascade takes the assignment with it.
    op.execute(
        "ALTER TABLE review_assignments "
        "ADD CONSTRAINT fk_review_assignments_assignee_membership "
        "FOREIGN KEY (assignee_user_id, workspace_id) "
        "REFERENCES workspace_members (user_id, workspace_id) ON DELETE CASCADE"
    )
    op.create_index(
        "ix_review_assignments_workspace_assignee",
        "review_assignments",
        ["workspace_id", "assignee_user_id"],
    )

    # ------------------------------------------------------------------
    # Indexes that make the union query answerable without a materialized view
    # ------------------------------------------------------------------
    op.create_index(
        "ix_dv_workspace_status_created",
        "document_verifications",
        ["workspace_id", "status", "created_at"],
    )
    op.create_index(
        "ix_ae_workspace_triage_created",
        "assertion_evaluations",
        ["workspace_id", "created_at"],
        postgresql_where=sa.text("routed_to = 'TRIAGE'"),
    )
    op.create_index(
        "ix_anomaly_findings_workspace_status_created",
        "anomaly_findings",
        ["workspace_id", "status", "created_at"],
    )

    # ------------------------------------------------------------------
    # review_queue_items
    # ------------------------------------------------------------------
    op.execute("DROP VIEW IF EXISTS review_queue_items")
    op.execute(REVIEW_QUEUE_VIEW)


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS review_queue_items")
    op.drop_index(
        "ix_anomaly_findings_workspace_status_created", table_name="anomaly_findings"
    )
    op.drop_index("ix_ae_workspace_triage_created", table_name="assertion_evaluations")
    op.drop_index("ix_dv_workspace_status_created", table_name="document_verifications")
    op.drop_table("review_assignments")
    op.drop_table("workspace_email_overrides")
    op.execute(
        "ALTER TABLE workspaces DROP CONSTRAINT IF EXISTS "
        "uq_workspaces_id_organization_id"
    )
    for name, _expression in AI_SETTINGS_CHECKS:
        op.execute(f"ALTER TABLE ai_settings DROP CONSTRAINT IF EXISTS {name}")
    for name, _type, _default in DEAD_AI_SETTINGS_COLUMNS:
        op.execute(f"UPDATE ai_settings SET {name} = DEFAULT WHERE {name} IS NULL")
        op.execute(f"ALTER TABLE ai_settings ALTER COLUMN {name} SET NOT NULL")
        op.execute(f"ALTER TABLE ai_settings ALTER COLUMN {name} DROP DEFAULT")

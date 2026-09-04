"""ARCH-26 Step 2 — warehouse_destinations, export_schedules, export_sync_runs (EXPAND)

Revision ID: arch26_step2_warehouse_sync
Revises: arch26_step1_export_vocabulary
Create Date: 2026-09-03

WHY `encrypted_credential` IS Text AND NOT String(512)
======================================================

Every other encrypted column in this schema — `organization_email_settings`,
`webhook_endpoints`, `provider_credentials` — is `String(512)`, matching
`MAX_CIPHERTEXT_LENGTH` in app/core/encryption.py. That bound exists because
those columns hold SMTP passwords, webhook signing secrets and provider API
keys, none of which legitimately exceed a few hundred characters, and a
ciphertext that outgrows its column is a defect worth catching at encrypt time
rather than at INSERT.

A warehouse credential is a different animal. A BigQuery service-account JSON
is roughly 2.3 KB and a Snowflake PKCS#8 private key roughly 1.7 KB. Both blow
through the 300-character plaintext ceiling by an order of magnitude, and
neither is compressible into one. ARCH-26 audit finding B1 resolved this by
adding `encrypt_secret` / `decrypt_secret` alongside the existing
`encrypt_password` rather than by raising the shared ceiling — raising it
would silently widen the guard protecting the String(512) columns above, and
the first oversized SMTP password would then fail at INSERT with a database
error instead of at encrypt with a message naming the field.

So: a distinct function, a distinct ceiling
(`MAX_SECRET_PLAINTEXT_LENGTH = 16384`), and an unbounded `Text` column here.
`String(n)` with a large n would be the worst of both — a bound nobody has
computed, enforced in the one place it cannot be explained.

WHY A DELETED DESTINATION LEAVES ITS RUNS BEHIND
================================================

`export_sync_runs.destination_id` is `ON DELETE SET NULL`, not CASCADE, and
the table carries a denormalised `destination_label` and `destination_kind`
alongside it.

A run row is a delivery receipt: it records that a set of rows left this
platform, on a date, with a content digest. CASCADE would mean a tenant
deleting a destination also erases the evidence of everything ever sent to it
— which is exactly the operation someone performs when they want that evidence
gone. The snapshot columns exist so the surviving row still answers "sent
where?" after the destination is gone.

`export_schedules.destination_id` IS CASCADE, because a schedule with no
destination is not a record of anything. It is a cron entry pointing at
nothing, and leaving it behind means the dispatcher has to defend against a
NULL destination on every tick.

WHY `ck_export_sync_runs_succeeded_has_digest` EXISTS
=====================================================

Hardening invariant 2 of ARCH-15, carried forward: the bundle a tenant
downloads is the bundle we digested. A run row with `status = 'SUCCEEDED'` and
`bundle_digest IS NULL` claims a delivery nobody can verify, and it is
produced by exactly one plausible bug — an exception swallowed between the
upload and the digest write, leaving the status update as the only thing that
landed. The constraint makes that state unrepresentable rather than merely
unlikely.

WHY THE COUNT COLUMNS ARE NULLABLE
==================================

Hardening invariant 6: unmeasured metrics export as NULL, never 0. A run that
died before counting rows has an unknown row count. Writing 0 there makes an
empty successful export and a crashed one look identical in the console, and
the person reading the run history is specifically trying to tell those two
apart. This is the same rule ARCH-24 applies to `cost_basis_micros` and for
the same reason.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "arch26_step2_warehouse_sync"
down_revision = "arch26_step1_export_vocabulary"
branch_labels = None
depends_on = None


#: Mirrors app.models.warehouse_sync.DESTINATION_KIND_VALUES. Duplicated on
#: purpose: a migration must be readable and runnable years from now without
#: importing application code that may have moved. verify_arch26.py G3 asserts
#: the lists are identical, so the duplication cannot drift silently.
DESTINATION_KIND_VALUES: tuple[str, ...] = (
    "SNOWFLAKE",
    "BIGQUERY",
    "DATABRICKS",
    "S3",
)

#: Mirrors app.models.warehouse_sync.DESTINATION_STATUS_VALUES.
DESTINATION_STATUS_VALUES: tuple[str, ...] = ("ACTIVE", "DISABLED")

#: Mirrors app.models.warehouse_sync.EXPORT_DATASET_VALUES.
#:
#: The vocabulary is closed and enforced in the service rather than by a
#: database CHECK, because the column is a JSONB array and a CHECK over array
#: membership in Postgres is a subquery-free expression only for fixed-length
#: cases. G6 asserts the service validates against this exact tuple.
EXPORT_DATASET_VALUES: tuple[str, ...] = (
    "USAGE_ROLLUPS",
    "DOCUMENT_METADATA",
    "ASSISTANT_TURNS",
    "AUTOMATION_RUNS",
)

#: Mirrors app.models.warehouse_sync.SCHEDULE_CADENCE_VALUES.
SCHEDULE_CADENCE_VALUES: tuple[str, ...] = ("DAILY", "WEEKLY", "MONTHLY")

#: Mirrors app.models.warehouse_sync.SYNC_TRIGGER_VALUES.
SYNC_TRIGGER_VALUES: tuple[str, ...] = ("SCHEDULED", "MANUAL")

#: Mirrors app.models.warehouse_sync.SYNC_STATUS_VALUES.
#:
#: PARTIAL is a real outcome and not a rounding of FAILED: a bundle carrying
#: three of four requested datasets was delivered and is digested, so the
#: tenant's warehouse now holds real rows. Reporting that as FAILED invites a
#: retry that duplicates the three that landed.
SYNC_STATUS_VALUES: tuple[str, ...] = (
    "RUNNING",
    "SUCCEEDED",
    "PARTIAL",
    "FAILED",
)

#: Consecutive failures before the schedule's circuit opens. Hardening
#: invariant 5: a failed sync alerts, it never silently retries forever.
CIRCUIT_FAILURE_THRESHOLD: int = 5

MAX_LABEL_LENGTH: int = 120
MAX_STORAGE_KEY_LENGTH: int = 512
MAX_DIGEST_LENGTH: int = 64

_KIND_SQL = ", ".join(f"'{v}'" for v in DESTINATION_KIND_VALUES)
_DEST_STATUS_SQL = ", ".join(f"'{v}'" for v in DESTINATION_STATUS_VALUES)
_CADENCE_SQL = ", ".join(f"'{v}'" for v in SCHEDULE_CADENCE_VALUES)
_TRIGGER_SQL = ", ".join(f"'{v}'" for v in SYNC_TRIGGER_VALUES)
_SYNC_STATUS_SQL = ", ".join(f"'{v}'" for v in SYNC_STATUS_VALUES)


def upgrade() -> None:
    # -----------------------------------------------------------------------
    # 1. warehouse_destinations
    # -----------------------------------------------------------------------
    op.create_table(
        "warehouse_destinations",
        sa.Column(
            "id",
            sa.UUID(as_uuid=True),
            primary_key=True,
            nullable=False,
        ),
        sa.Column(
            "organization_id",
            sa.UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
            comment="Owning tenant. Every read path filters on this column; "
            "there is no cross-tenant query in this phase.",
        ),
        sa.Column(
            "label",
            sa.String(length=MAX_LABEL_LENGTH),
            nullable=False,
            comment="Tenant-chosen display name. Unique within the tenant so "
            "a run history entry naming 'Prod warehouse' is unambiguous.",
        ),
        sa.Column(
            "kind",
            sa.String(length=16),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.String(length=16),
            nullable=False,
            server_default=sa.text("'ACTIVE'"),
        ),
        sa.Column(
            "config",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
            comment="Non-secret connection parameters only: account locator, "
            "warehouse, database, schema, project, dataset, host, http_path, "
            "bucket, region, prefix. The secret half lives in "
            "encrypted_credential and never in here.",
        ),
        sa.Column(
            "encrypted_credential",
            sa.Text(),
            nullable=False,
            comment="MultiFernet ciphertext from app.core.encryption."
            "encrypt_secret. Text and not String(512) because a BigQuery "
            "service-account JSON is ~2.3KB. Never returned by any endpoint.",
        ),
        sa.Column(
            "credential_fingerprint",
            sa.String(length=12),
            nullable=False,
            comment="First 12 hex of SHA-256 over the plaintext credential. "
            "Displayable: it lets a tenant confirm which key is installed "
            "without the key being readable back. Not a secret, and not "
            "sufficient to recover one.",
        ),
        sa.Column(
            "last_tested_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "last_test_ok",
            sa.Boolean(),
            nullable=True,
            comment="NULL means never probed. Distinct from False, which "
            "means probed and refused — invariant 6 applied to a boolean.",
        ),
        sa.Column(
            "last_test_error",
            sa.String(length=500),
            nullable=True,
        ),
        sa.Column(
            "created_by_id",
            sa.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )

    # Check constraints are created separately, with the fully qualified
    # convention name spelled out. `op.create_table` builds its Table in a
    # temporary MetaData that does NOT carry app.db.base.NAMING_CONVENTION, so
    # a constraint declared inline as name="kind_known" lands in the database
    # as `kind_known` while the model — whose MetaData does carry the
    # convention — expects `ck_warehouse_destinations_kind_known`. Autogenerate
    # then proposes dropping and recreating every one of them, forever.
    #
    # This is the ARCH-25 precedent (arch25_step2_custom_domains) and G14
    # fails if it is not followed.
    for _name, _condition in (
        ("kind_known", f"kind IN ({_KIND_SQL})"),
        ("status_known", f"status IN ({_DEST_STATUS_SQL})"),
        ("credential_present", "char_length(encrypted_credential) > 0"),
        ("label_present", "char_length(label) > 0"),
    ):
        op.create_check_constraint(
            f"ck_warehouse_destinations_{_name}",
            "warehouse_destinations",
            _condition,
        )

    op.create_index(
        "uq_warehouse_destinations_org_label",
        "warehouse_destinations",
        ["organization_id", "label"],
        unique=True,
    )
    op.create_index(
        "ix_warehouse_destinations_organization_id",
        "warehouse_destinations",
        ["organization_id"],
    )
    op.create_index(
        "ix_warehouse_destinations_active",
        "warehouse_destinations",
        ["organization_id", "kind"],
        postgresql_where=sa.text("status = 'ACTIVE'"),
    )

    # -----------------------------------------------------------------------
    # 2. export_schedules
    # -----------------------------------------------------------------------
    op.create_table(
        "export_schedules",
        sa.Column(
            "id",
            sa.UUID(as_uuid=True),
            primary_key=True,
            nullable=False,
        ),
        sa.Column(
            "organization_id",
            sa.UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "destination_id",
            sa.UUID(as_uuid=True),
            sa.ForeignKey("warehouse_destinations.id", ondelete="CASCADE"),
            nullable=False,
            comment="CASCADE: a schedule with no destination is a cron entry "
            "pointing at nothing, and leaving it behind forces the dispatcher "
            "to defend against a NULL destination on every tick.",
        ),
        sa.Column(
            "datasets",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            comment="Array of EXPORT_DATASET_VALUES. Validated in the service "
            "rather than by a CHECK: array membership over a closed set is not "
            "expressible as a subquery-free CHECK in Postgres.",
        ),
        sa.Column(
            "cadence",
            sa.String(length=16),
            nullable=False,
        ),
        sa.Column(
            "hour_utc",
            sa.SmallInteger(),
            nullable=False,
            server_default=sa.text("2"),
            comment="UTC and not tenant-local. A local hour moves twice a year "
            "and the run that lands in the repeated hour runs twice.",
        ),
        sa.Column("day_of_week", sa.SmallInteger(), nullable=True),
        sa.Column("day_of_month", sa.SmallInteger(), nullable=True),
        sa.Column(
            "lookback_days",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("1"),
            comment="Window width. Deliberately re-exports overlapping days: "
            "the warehouse side is idempotent on (bundle digest, row key), and "
            "a gap is far more expensive to notice than a duplicate.",
        ),
        sa.Column(
            "enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column(
            "consecutive_failure_count",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "circuit_opened_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="Set when consecutive_failure_count reaches "
            f"{CIRCUIT_FAILURE_THRESHOLD}. A non-NULL value stops the "
            "dispatcher and raises an operator alert. Cleared only by a "
            "successful manual run — hardening invariant 5.",
        ),
        sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )

    for _name, _condition in (
        ("cadence_known", f"cadence IN ({_CADENCE_SQL})"),
        ("hour_in_range", "hour_utc >= 0 AND hour_utc <= 23"),
        (
            "weekly_needs_day_of_week",
            "cadence <> 'WEEKLY' OR day_of_week IS NOT NULL",
        ),
        (
            "monthly_needs_day_of_month",
            "cadence <> 'MONTHLY' OR day_of_month IS NOT NULL",
        ),
        (
            "day_of_week_in_range",
            "day_of_week IS NULL OR (day_of_week >= 0 AND day_of_week <= 6)",
        ),
        # 28 and not 31. A schedule on the 31st does not run in February, and
        # the tenant discovers the hole in a board deck rather than in a log.
        (
            "day_of_month_in_range",
            "day_of_month IS NULL OR (day_of_month >= 1 AND day_of_month <= 28)",
        ),
        ("lookback_in_range", "lookback_days >= 1 AND lookback_days <= 90"),
        ("failure_count_non_negative", "consecutive_failure_count >= 0"),
        (
            "datasets_non_empty_array",
            "jsonb_typeof(datasets) = 'array' AND "
            "jsonb_array_length(datasets) > 0",
        ),
    ):
        op.create_check_constraint(
            f"ck_export_schedules_{_name}", "export_schedules", _condition
        )

    op.create_index(
        "uq_export_schedules_destination_cadence",
        "export_schedules",
        ["destination_id", "cadence"],
        unique=True,
    )
    op.create_index(
        "ix_export_schedules_organization_id",
        "export_schedules",
        ["organization_id"],
    )
    op.create_index(
        "ix_export_schedules_due",
        "export_schedules",
        ["next_run_at"],
        postgresql_where=sa.text(
            "enabled = true AND circuit_opened_at IS NULL"
        ),
    )

    # -----------------------------------------------------------------------
    # 3. export_sync_runs
    # -----------------------------------------------------------------------
    op.create_table(
        "export_sync_runs",
        sa.Column(
            "id",
            sa.UUID(as_uuid=True),
            primary_key=True,
            nullable=False,
        ),
        sa.Column(
            "organization_id",
            sa.UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "destination_id",
            sa.UUID(as_uuid=True),
            sa.ForeignKey("warehouse_destinations.id", ondelete="SET NULL"),
            nullable=True,
            comment="SET NULL and not CASCADE. A run row is a delivery "
            "receipt; CASCADE would let deleting a destination erase the "
            "evidence of everything ever sent to it.",
        ),
        sa.Column(
            "schedule_id",
            sa.UUID(as_uuid=True),
            sa.ForeignKey("export_schedules.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "destination_label",
            sa.String(length=MAX_LABEL_LENGTH),
            nullable=False,
            comment="Snapshot. Keeps the receipt answering 'sent where?' "
            "after destination_id is nulled.",
        ),
        sa.Column(
            "destination_kind",
            sa.String(length=16),
            nullable=False,
        ),
        sa.Column("trigger", sa.String(length=16), nullable=False),
        sa.Column(
            "status",
            sa.String(length=16),
            nullable=False,
            server_default=sa.text("'RUNNING'"),
        ),
        sa.Column(
            "datasets",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "window_start", sa.DateTime(timezone=True), nullable=False
        ),
        sa.Column("window_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "row_count",
            sa.BigInteger(),
            nullable=True,
            comment="NULL means not counted, which is what a crashed run has. "
            "Writing 0 makes an empty export and a crashed one identical in "
            "the console — invariant 6.",
        ),
        sa.Column("byte_count", sa.BigInteger(), nullable=True),
        sa.Column("part_count", sa.Integer(), nullable=True),
        sa.Column(
            "bundle_digest",
            sa.String(length=MAX_DIGEST_LENGTH),
            nullable=True,
            comment="SHA-256 over the sorted per-part digests. The receipt "
            "that makes 'the bundle you downloaded is the bundle we digested' "
            "checkable, mirroring ARCH-15 invoice digests.",
        ),
        sa.Column(
            "manifest_key",
            sa.String(length=MAX_STORAGE_KEY_LENGTH),
            nullable=True,
        ),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("error_detail", sa.String(length=1000), nullable=True),
        sa.Column(
            "attempt",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("1"),
        ),
        sa.Column(
            "triggered_by_id",
            sa.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )

    for _name, _condition in (
        ("trigger_known", f"trigger IN ({_TRIGGER_SQL})"),
        ("status_known", f"status IN ({_SYNC_STATUS_SQL})"),
        ("kind_known", f"destination_kind IN ({_KIND_SQL})"),
        ("window_ordered", "window_end > window_start"),
        ("row_count_non_negative", "row_count IS NULL OR row_count >= 0"),
        ("byte_count_non_negative", "byte_count IS NULL OR byte_count >= 0"),
        ("part_count_non_negative", "part_count IS NULL OR part_count >= 0"),
        ("attempt_positive", "attempt >= 1"),
        # A success without a digest claims a delivery nobody can verify.
        (
            "succeeded_has_digest",
            "(status = 'SUCCEEDED') = (bundle_digest IS NOT NULL)",
        ),
        (
            "finished_matches_status",
            "(status = 'RUNNING') = (finished_at IS NULL)",
        ),
        (
            "failed_has_error_code",
            "status <> 'FAILED' OR error_code IS NOT NULL",
        ),
    ):
        op.create_check_constraint(
            f"ck_export_sync_runs_{_name}", "export_sync_runs", _condition
        )

    op.create_index(
        "ix_export_sync_runs_org_started",
        "export_sync_runs",
        ["organization_id", sa.text("started_at DESC")],
    )
    op.create_index(
        "ix_export_sync_runs_destination",
        "export_sync_runs",
        ["destination_id"],
        postgresql_where=sa.text("destination_id IS NOT NULL"),
    )
    op.create_index(
        "ix_export_sync_runs_failed",
        "export_sync_runs",
        ["organization_id", sa.text("started_at DESC")],
        postgresql_where=sa.text("status = 'FAILED'"),
    )
    op.create_index(
        "ix_export_sync_runs_schedule",
        "export_sync_runs",
        ["schedule_id"],
        postgresql_where=sa.text("schedule_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_export_sync_runs_schedule", table_name="export_sync_runs")
    op.drop_index("ix_export_sync_runs_failed", table_name="export_sync_runs")
    op.drop_index(
        "ix_export_sync_runs_destination", table_name="export_sync_runs"
    )
    op.drop_index("ix_export_sync_runs_org_started", table_name="export_sync_runs")
    op.drop_table("export_sync_runs")

    op.drop_index("ix_export_schedules_due", table_name="export_schedules")
    op.drop_index(
        "ix_export_schedules_organization_id", table_name="export_schedules"
    )
    op.drop_index(
        "uq_export_schedules_destination_cadence", table_name="export_schedules"
    )
    op.drop_table("export_schedules")

    op.drop_index(
        "ix_warehouse_destinations_active", table_name="warehouse_destinations"
    )
    op.drop_index(
        "ix_warehouse_destinations_organization_id",
        table_name="warehouse_destinations",
    )
    op.drop_index(
        "uq_warehouse_destinations_org_label",
        table_name="warehouse_destinations",
    )
    op.drop_table("warehouse_destinations")
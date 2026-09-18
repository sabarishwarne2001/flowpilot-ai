"""ARCH-38 Step 1 — batch ingestion, upload sessions, tags, presets, holds (EXPAND)

Revision ID: arch38_step1_batches
Revises: arch38_step0_batch_vocabulary
Create Date: 2026-09-17

EXPAND ONLY. Nothing here drops or narrows an existing column. The one change
to a table that already exists is an additive UNIQUE index on
`work_items (id, workspace_id)`, which is what lets the two child tables below
carry a composite foreign key.

WHY COMPOSITE FOREIGN KEYS RATHER THAN A CHECK
==============================================

The gate is "a cross-workspace batch item is refused, by the database and by
the service". A CHECK constraint is single-row: it cannot read the parent's
workspace_id, so it cannot express that constraint at all. A trigger could,
at the cost of a function to maintain and a per-row call.

A composite foreign key expresses it declaratively:

    ingestion_batch_items (batch_id, workspace_id)
        -> ingestion_batches (id, workspace_id)

An item whose workspace_id disagrees with its batch's has no parent row to
point at, so PostgreSQL refuses the INSERT with 23503. The same shape is used
for `ingestion_batch_items.work_item_id` and for `work_item_tags`, so a tag
cannot be attached to another tenant's document either.

CHECK CONSTRAINT NAMES
======================

`op.create_check_constraint` and `sa.CheckConstraint` inside `op.create_table`
both pass through the metadata naming convention and emit
`ck_<table>_ck_<table>_...`. Every CHECK below is therefore added with a raw
`ALTER TABLE ... ADD CONSTRAINT <exact name>` so `verify_arch38.py` can find it
by the name it expects. This is the ARCH-31/34/37 pattern.
"""

from __future__ import annotations

import json
import uuid

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "arch38_step1_batches"
down_revision = "arch38_step0_batch_vocabulary"
branch_labels = None
depends_on = None


BATCH_STATUSES = (
    "OPEN",
    "UPLOADING",
    "PROCESSING",
    "COMPLETED",
    "COMPLETED_WITH_ERRORS",
    "CANCELLED",
)
ITEM_STATUSES = (
    "PENDING",
    "UPLOADING",
    "UPLOADED",
    "PROCESSING",
    "COMPLETED",
    "FAILED",
    "CANCELLED",
)
SESSION_STATUSES = ("ACTIVE", "COMPLETED", "ABORTED", "EXPIRED")
BATCH_SOURCES = ("FILES", "FOLDER", "ARCHIVE")

#: The synthetic organization id a platform preset (organization_id IS NULL)
#: collapses to in the uniqueness index. A NULL in a unique index does not
#: collide with another NULL, so without this two platform presets could share
#: (document_type, version).
ZERO_UUID = "00000000-0000-0000-0000-000000000000"

TAG_PATTERN = "^[a-z0-9][a-z0-9_-]{0,47}$"


def _sql_list(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def _check(table: str, name: str, expression: str) -> None:
    """Add a CHECK under the exact name the gates look for."""
    op.execute(f"ALTER TABLE {table} ADD CONSTRAINT {name} CHECK ({expression})")


# ---------------------------------------------------------------------------
# Platform preset packs (ARCH-38 §5: HR, Healthcare, Legal, Logistics/KYC)
# ---------------------------------------------------------------------------


def _field(key: str, kind: str, description: str) -> dict[str, object]:
    return {"type": kind, "title": key.replace("_", " ").title(), "description": description}


def _schema(properties: dict[str, dict[str, object]], required: list[str]) -> dict[str, object]:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": True,
    }


PLATFORM_PRESETS: tuple[dict[str, object], ...] = (
    {
        "industry": "HR",
        "document_type": "resume",
        "label": "Résumé / CV",
        "description": "Candidate name, contact details, employment history and skills.",
        "redaction_profile": "all_identifiers",
        "classifier_hints": ["curriculum vitae", "resume", "work experience", "education"],
        "schema": _schema(
            {
                "candidate_name": _field("candidate_name", "string", "Full name as written."),
                "email": _field("email", "string", "Primary contact email."),
                "phone": _field("phone", "string", "Primary contact number."),
                "years_experience": _field("years_experience", "number", "Total years of professional experience."),
                "most_recent_employer": _field("most_recent_employer", "string", "Most recent employer."),
                "skills": {"type": "array", "title": "Skills", "items": {"type": "string"}},
            },
            ["candidate_name"],
        ),
        "assertions": [
            {"sentence": "The candidate name is present.", "severity": "HIGH"},
            {"sentence": "At least one contact method is present.", "severity": "MEDIUM"},
        ],
    },
    {
        "industry": "HR",
        "document_type": "offer_letter",
        "label": "Offer letter",
        "description": "Role, start date, compensation and signature block.",
        "redaction_profile": "all_identifiers",
        "classifier_hints": ["offer of employment", "we are pleased to offer", "start date"],
        "schema": _schema(
            {
                "candidate_name": _field("candidate_name", "string", "Person the offer is made to."),
                "job_title": _field("job_title", "string", "Role offered."),
                "start_date": _field("start_date", "string", "Stated start date."),
                "annual_compensation": _field("annual_compensation", "number", "Annual gross compensation."),
                "currency": _field("currency", "string", "ISO currency code."),
            },
            ["candidate_name", "job_title"],
        ),
        "assertions": [
            {"sentence": "The start date is within 180 days of the offer date.", "severity": "MEDIUM"},
        ],
    },
    {
        "industry": "HEALTHCARE",
        "document_type": "intake_form",
        "label": "Patient intake form",
        "description": "Patient-supplied demographics and presenting complaint.",
        "redaction_profile": "hipaa_safe_harbor",
        "classifier_hints": ["patient intake", "date of birth", "presenting complaint"],
        "schema": _schema(
            {
                "patient_name": _field("patient_name", "string", "Patient's full name."),
                "date_of_birth": _field("date_of_birth", "string", "Date of birth as written."),
                "record_number": _field("record_number", "string", "Medical record number."),
                "presenting_complaint": _field("presenting_complaint", "string", "Reason for the visit."),
            },
            ["patient_name"],
        ),
        "assertions": [
            {"sentence": "A date of birth is present.", "severity": "HIGH"},
        ],
    },
    {
        "industry": "HEALTHCARE",
        "document_type": "discharge_summary",
        "label": "Discharge summary",
        "description": "Admission and discharge dates, diagnosis and follow-up plan.",
        "redaction_profile": "hipaa_safe_harbor",
        "classifier_hints": ["discharge summary", "date of admission", "follow-up"],
        "schema": _schema(
            {
                "patient_name": _field("patient_name", "string", "Patient's full name."),
                "admission_date": _field("admission_date", "string", "Date of admission."),
                "discharge_date": _field("discharge_date", "string", "Date of discharge."),
                "primary_diagnosis": _field("primary_diagnosis", "string", "Primary diagnosis at discharge."),
            },
            ["patient_name", "discharge_date"],
        ),
        "assertions": [
            {"sentence": "The discharge date is on or after the admission date.", "severity": "HIGH"},
        ],
    },
    {
        "industry": "LEGAL",
        "document_type": "nda",
        "label": "Non-disclosure agreement",
        "description": "Parties, effective date, term and governing law.",
        "redaction_profile": "financial",
        "classifier_hints": ["non-disclosure", "confidential information", "receiving party"],
        "schema": _schema(
            {
                "disclosing_party": _field("disclosing_party", "string", "Party disclosing information."),
                "receiving_party": _field("receiving_party", "string", "Party receiving information."),
                "effective_date": _field("effective_date", "string", "Effective date."),
                "term_months": _field("term_months", "number", "Confidentiality period in months."),
                "governing_law": _field("governing_law", "string", "Governing law."),
            },
            ["disclosing_party", "receiving_party"],
        ),
        "assertions": [
            {"sentence": "The confidentiality period is at least 24 months.", "severity": "MEDIUM"},
            {"sentence": "A governing law is named.", "severity": "MEDIUM"},
        ],
    },
    {
        "industry": "LEGAL",
        "document_type": "msa",
        "label": "Master services agreement",
        "description": "Parties, term, payment terms, liability cap and termination.",
        "redaction_profile": "financial",
        "classifier_hints": ["master services agreement", "statement of work", "limitation of liability"],
        "schema": _schema(
            {
                "customer": _field("customer", "string", "Customer entity."),
                "supplier": _field("supplier", "string", "Supplier entity."),
                "effective_date": _field("effective_date", "string", "Effective date."),
                "payment_terms_days": _field("payment_terms_days", "number", "Payment terms in days."),
                "liability_cap": _field("liability_cap", "number", "Aggregate liability cap."),
            },
            ["customer", "supplier"],
        ),
        "assertions": [
            {"sentence": "Payment terms are no longer than 60 days.", "severity": "HIGH"},
            {"sentence": "A limitation of liability is present.", "severity": "HIGH"},
        ],
    },
    {
        "industry": "LEGAL",
        "document_type": "lease",
        "label": "Lease agreement",
        "description": "Premises, parties, rent, term and renewal.",
        "redaction_profile": "financial",
        "classifier_hints": ["lease", "landlord", "tenant", "demised premises"],
        "schema": _schema(
            {
                "landlord": _field("landlord", "string", "Landlord."),
                "tenant": _field("tenant", "string", "Tenant."),
                "premises": _field("premises", "string", "Description of the premises."),
                "monthly_rent": _field("monthly_rent", "number", "Monthly rent."),
                "term_months": _field("term_months", "number", "Lease term in months."),
            },
            ["landlord", "tenant"],
        ),
        "assertions": [
            {"sentence": "The lease term is at least 12 months.", "severity": "LOW"},
        ],
    },
    {
        "industry": "LOGISTICS",
        "document_type": "bill_of_lading",
        "label": "Bill of lading",
        "description": "Shipper, consignee, carrier, container and goods description.",
        "redaction_profile": "financial",
        "classifier_hints": ["bill of lading", "shipper", "consignee", "port of discharge"],
        "schema": _schema(
            {
                "bl_number": _field("bl_number", "string", "Bill of lading number."),
                "shipper": _field("shipper", "string", "Shipper."),
                "consignee": _field("consignee", "string", "Consignee."),
                "port_of_loading": _field("port_of_loading", "string", "Port of loading."),
                "port_of_discharge": _field("port_of_discharge", "string", "Port of discharge."),
                "container_numbers": {"type": "array", "title": "Container Numbers", "items": {"type": "string"}},
            },
            ["bl_number", "shipper", "consignee"],
        ),
        "assertions": [
            {"sentence": "A bill of lading number is present.", "severity": "HIGH"},
        ],
    },
    {
        "industry": "LOGISTICS",
        "document_type": "customs_manifest",
        "label": "Customs manifest",
        "description": "Declaration number, HS codes, declared value and origin.",
        "redaction_profile": "financial",
        "classifier_hints": ["customs", "manifest", "hs code", "country of origin"],
        "schema": _schema(
            {
                "declaration_number": _field("declaration_number", "string", "Customs declaration number."),
                "country_of_origin": _field("country_of_origin", "string", "Country of origin."),
                "declared_value": _field("declared_value", "number", "Total declared value."),
                "currency": _field("currency", "string", "ISO currency code."),
            },
            ["declaration_number"],
        ),
        "assertions": [
            {"sentence": "A declared value is present.", "severity": "HIGH"},
        ],
    },
    {
        "industry": "KYC",
        "document_type": "passport",
        "label": "Passport",
        "description": "Machine-readable identity page.",
        "redaction_profile": "all_identifiers",
        "classifier_hints": ["passport", "machine readable zone", "date of expiry"],
        "schema": _schema(
            {
                "holder_name": _field("holder_name", "string", "Name as printed."),
                "passport_number": _field("passport_number", "string", "Passport number."),
                "nationality": _field("nationality", "string", "Nationality."),
                "date_of_expiry": _field("date_of_expiry", "string", "Date of expiry."),
            },
            ["holder_name", "passport_number"],
        ),
        "assertions": [
            {"sentence": "The document is not expired.", "severity": "HIGH"},
        ],
    },
    {
        "industry": "KYC",
        "document_type": "india_id_card",
        "label": "Aadhaar / PAN card",
        "description": "Indian identity card. Numbers are redacted by default.",
        "redaction_profile": "india_kyc",
        "classifier_hints": ["aadhaar", "permanent account number", "unique identification"],
        "schema": _schema(
            {
                "holder_name": _field("holder_name", "string", "Name as printed."),
                "id_type": _field("id_type", "string", "AADHAAR or PAN."),
                "date_of_birth": _field("date_of_birth", "string", "Date of birth as printed."),
            },
            ["holder_name", "id_type"],
        ),
        "assertions": [
            {"sentence": "The holder name is present.", "severity": "HIGH"},
        ],
    },
)


def upgrade() -> None:
    # -- work_items: the target of two composite foreign keys ---------------
    #
    # Additive. `work_items.id` is already the primary key, so this index adds
    # no new uniqueness rule -- it only gives PostgreSQL a unique constraint on
    # the *pair*, which a composite FK requires as its referenced target.
    op.execute(
        "ALTER TABLE work_items "
        "ADD CONSTRAINT uq_work_items_id_workspace_id UNIQUE (id, workspace_id)"
    )

    # -- ingestion_batches --------------------------------------------------
    op.create_table(
        "ingestion_batches",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="OPEN"),
        sa.Column("source", sa.String(16), nullable=False, server_default="FILES"),
        sa.Column("total_items", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("completed_items", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failed_items", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name="fk_ingestion_batches_workspace_id_workspaces",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            name="fk_ingestion_batches_created_by_user_id_users",
            ondelete="SET NULL",
        ),
        sa.UniqueConstraint(
            "id", "workspace_id", name="uq_ingestion_batches_id_workspace_id"
        ),
    )
    _check(
        "ingestion_batches",
        "ck_ingestion_batches_status",
        f"status IN ({_sql_list(BATCH_STATUSES)})",
    )
    _check(
        "ingestion_batches",
        "ck_ingestion_batches_source",
        f"source IN ({_sql_list(BATCH_SOURCES)})",
    )
    _check(
        "ingestion_batches",
        "ck_ingestion_batches_counts_nonnegative",
        "total_items >= 0 AND completed_items >= 0 AND failed_items >= 0",
    )
    _check(
        "ingestion_batches",
        "ck_ingestion_batches_counts_bounded",
        "completed_items + failed_items <= total_items",
    )
    op.create_index(
        "ix_ingestion_batches_workspace_created",
        "ingestion_batches",
        ["workspace_id", "created_at"],
    )

    # -- upload_sessions ----------------------------------------------------
    op.create_table(
        "upload_sessions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("object_key", sa.String(512), nullable=False),
        sa.Column("minio_upload_id", sa.String(255), nullable=True),
        sa.Column("part_size", sa.Integer(), nullable=False),
        sa.Column("total_size", sa.BigInteger(), nullable=True),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("mime_type", sa.String(128), nullable=True),
        sa.Column(
            "parts_received",
            postgresql.ARRAY(sa.Integer()),
            nullable=False,
            server_default=sa.text("'{}'::int[]"),
        ),
        sa.Column("expected_sha256", sa.String(64), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="ACTIVE"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name="fk_upload_sessions_workspace_id_workspaces",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name="fk_upload_sessions_organization_id_organizations",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            name="fk_upload_sessions_created_by_user_id_users",
            ondelete="SET NULL",
        ),
        sa.UniqueConstraint(
            "id", "workspace_id", name="uq_upload_sessions_id_workspace_id"
        ),
    )
    _check(
        "upload_sessions",
        "ck_upload_sessions_status",
        f"status IN ({_sql_list(SESSION_STATUSES)})",
    )
    # 5 MiB is the S3 minimum for every part but the last; 64 MiB is this
    # product's ceiling, so a client cannot ask a worker to hold more.
    _check(
        "upload_sessions",
        "ck_upload_sessions_part_size",
        "part_size >= 5242880 AND part_size <= 67108864",
    )
    _check(
        "upload_sessions",
        "ck_upload_sessions_sha256_format",
        "expected_sha256 IS NULL OR expected_sha256 ~ '^[0-9a-f]{64}$'",
    )
    op.create_index(
        "ix_upload_sessions_workspace_status",
        "upload_sessions",
        ["workspace_id", "status"],
    )
    op.create_index(
        "ix_upload_sessions_expiry",
        "upload_sessions",
        ["expires_at"],
        postgresql_where=sa.text("status = 'ACTIVE'"),
    )

    # -- ingestion_batch_items ---------------------------------------------
    op.create_table(
        "ingestion_batch_items",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("batch_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("client_key", sa.String(200), nullable=False),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("sha256", sa.String(64), nullable=True),
        sa.Column("upload_session_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("work_item_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("status", sa.String(24), nullable=False, server_default="PENDING"),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.Column("error_detail", sa.Text(), nullable=True),
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
        # The cross-workspace refusal. An item whose workspace_id disagrees
        # with its batch's has no parent row to reference.
        sa.ForeignKeyConstraint(
            ["batch_id", "workspace_id"],
            ["ingestion_batches.id", "ingestion_batches.workspace_id"],
            name="fk_ingestion_batch_items_batch_workspace",
            ondelete="CASCADE",
        ),
        # Same shape for the document: a batch item cannot point at another
        # tenant's work item.
        sa.ForeignKeyConstraint(
            ["work_item_id", "workspace_id"],
            ["work_items.id", "work_items.workspace_id"],
            name="fk_ingestion_batch_items_work_item_workspace",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["upload_session_id", "workspace_id"],
            ["upload_sessions.id", "upload_sessions.workspace_id"],
            name="fk_ingestion_batch_items_session_workspace",
            ondelete="SET NULL",
        ),
        sa.UniqueConstraint(
            "batch_id", "client_key", name="uq_ingestion_batch_items_batch_client_key"
        ),
    )
    _check(
        "ingestion_batch_items",
        "ck_ingestion_batch_items_status",
        f"status IN ({_sql_list(ITEM_STATUSES)})",
    )
    _check(
        "ingestion_batch_items",
        "ck_ingestion_batch_items_sha256_format",
        "sha256 IS NULL OR sha256 ~ '^[0-9a-f]{64}$'",
    )
    _check(
        "ingestion_batch_items",
        "ck_ingestion_batch_items_failed_has_code",
        "status <> 'FAILED' OR error_code IS NOT NULL",
    )
    op.create_index(
        "ix_ingestion_batch_items_batch_status",
        "ingestion_batch_items",
        ["batch_id", "status"],
    )

    # -- work_item_tags -----------------------------------------------------
    op.create_table(
        "work_item_tags",
        sa.Column("work_item_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tag", sa.String(48), nullable=False),
        sa.Column("created_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("work_item_id", "tag", name="pk_work_item_tags"),
        sa.ForeignKeyConstraint(
            ["work_item_id", "workspace_id"],
            ["work_items.id", "work_items.workspace_id"],
            name="fk_work_item_tags_work_item_workspace",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            name="fk_work_item_tags_created_by_user_id_users",
            ondelete="SET NULL",
        ),
    )
    _check(
        "work_item_tags",
        "ck_work_item_tags_tag_format",
        f"tag ~ '{TAG_PATTERN}'",
    )
    op.create_index(
        "ix_work_item_tags_workspace_tag",
        "work_item_tags",
        ["workspace_id", "tag"],
    )

    # -- document_schema_presets -------------------------------------------
    op.create_table(
        "document_schema_presets",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("industry", sa.String(32), nullable=False),
        sa.Column("document_type", sa.String(64), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("label", sa.String(128), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("schema", postgresql.JSONB(), nullable=False),
        sa.Column(
            "assertions",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "classifier_hints",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("redaction_profile", sa.String(48), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name="fk_document_schema_presets_organization_id_organizations",
            ondelete="CASCADE",
        ),
    )
    _check(
        "document_schema_presets",
        "ck_document_schema_presets_schema_object",
        "jsonb_typeof(schema) = 'object'",
    )
    _check(
        "document_schema_presets",
        "ck_document_schema_presets_assertions_array",
        "jsonb_typeof(assertions) = 'array'",
    )
    _check(
        "document_schema_presets",
        "ck_document_schema_presets_version_positive",
        "version >= 1",
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_document_schema_presets_scope_type_version "
        "ON document_schema_presets "
        f"(coalesce(organization_id, '{ZERO_UUID}'::uuid), document_type, version)"
    )

    # -- workspace_schema_presets ------------------------------------------
    #
    # Not in the blueprint DDL, and required by its own UI paragraph: "Apply to
    # the workspace, then review the field list, assertions and redaction
    # profile before enabling." Presets are platform- or organization-scoped;
    # applying and enabling are per-workspace acts, so they need a row of their
    # own. Without it there is nowhere to record that a workspace applied a
    # preset but has not enabled it yet, which is exactly the state the review
    # step exists to create.
    op.create_table(
        "workspace_schema_presets",
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("preset_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "enabled", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        sa.Column("applied_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "applied_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("enabled_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint(
            "workspace_id", "preset_id", name="pk_workspace_schema_presets"
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name="fk_workspace_schema_presets_workspace_id_workspaces",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["preset_id"],
            ["document_schema_presets.id"],
            name="fk_workspace_schema_presets_preset_id_presets",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["applied_by_user_id"],
            ["users.id"],
            name="fk_workspace_schema_presets_applied_by_user_id_users",
            ondelete="SET NULL",
        ),
    )
    _check(
        "workspace_schema_presets",
        "ck_workspace_schema_presets_enabled_timestamp",
        "enabled = false OR enabled_at IS NOT NULL",
    )

    # -- retention_holds ----------------------------------------------------
    #
    # ARCH-20 ships `retention_policies` (an age floor below which the sweeper
    # will not purge) and no hold of any kind. "Deletes respect ARCH-20
    # retention holds" therefore had no mechanism to respect. A hold is the
    # enterprise meaning of the phrase -- a named, audited reason a specific
    # document or workspace may not be deleted until somebody releases it --
    # and `retention_service` enforces both it and the policy floor.
    op.create_table(
        "retention_holds",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("work_item_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("reason", sa.String(255), nullable=False),
        sa.Column("reference", sa.String(128), nullable=True),
        sa.Column("placed_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "placed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("released_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("released_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name="fk_retention_holds_organization_id_organizations",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name="fk_retention_holds_workspace_id_workspaces",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["work_item_id"],
            ["work_items.id"],
            name="fk_retention_holds_work_item_id_work_items",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["placed_by_user_id"],
            ["users.id"],
            name="fk_retention_holds_placed_by_user_id_users",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["released_by_user_id"],
            ["users.id"],
            name="fk_retention_holds_released_by_user_id_users",
            ondelete="SET NULL",
        ),
    )
    # A hold with no scope would hold nothing, which reads as "no hold" to
    # every query and as "protected" to whoever placed it.
    _check(
        "retention_holds",
        "ck_retention_holds_scope_present",
        "workspace_id IS NOT NULL OR work_item_id IS NOT NULL",
    )
    op.create_index(
        "ix_retention_holds_active_work_item",
        "retention_holds",
        ["work_item_id"],
        postgresql_where=sa.text("released_at IS NULL AND work_item_id IS NOT NULL"),
    )
    op.create_index(
        "ix_retention_holds_active_workspace",
        "retention_holds",
        ["workspace_id"],
        postgresql_where=sa.text("released_at IS NULL AND workspace_id IS NOT NULL"),
    )

    _seed_platform_presets()


def _seed_platform_presets() -> None:
    """Insert the platform packs (organization_id IS NULL).

    Idempotent through the uniqueness index: re-running on a database that
    already has them is a no-op rather than a duplicate-key failure.
    """
    connection = op.get_bind()
    statement = sa.text(
        """
        INSERT INTO document_schema_presets
            (id, organization_id, industry, document_type, version, label,
             description, schema, assertions, classifier_hints,
             redaction_profile)
        VALUES
            (:id, NULL, :industry, :document_type, 1, :label, :description,
             CAST(:schema AS jsonb), CAST(:assertions AS jsonb),
             CAST(:hints AS jsonb), :redaction_profile)
        ON CONFLICT DO NOTHING
        """
    )
    for preset in PLATFORM_PRESETS:
        connection.execute(
            statement,
            {
                "id": str(uuid.uuid4()),
                "industry": preset["industry"],
                "document_type": preset["document_type"],
                "label": preset["label"],
                "description": preset["description"],
                "schema": json.dumps(preset["schema"]),
                "assertions": json.dumps(preset["assertions"]),
                "hints": json.dumps(preset["classifier_hints"]),
                "redaction_profile": preset["redaction_profile"],
            },
        )


def downgrade() -> None:
    op.drop_table("retention_holds")
    op.drop_table("workspace_schema_presets")
    op.execute("DROP INDEX IF EXISTS uq_document_schema_presets_scope_type_version")
    op.drop_table("document_schema_presets")
    op.drop_table("work_item_tags")
    op.drop_table("ingestion_batch_items")
    op.drop_table("upload_sessions")
    op.drop_table("ingestion_batches")
    op.execute(
        "ALTER TABLE work_items DROP CONSTRAINT IF EXISTS uq_work_items_id_workspace_id"
    )

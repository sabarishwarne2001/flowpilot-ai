"""ARCH-22 Step 2 — tenant provider credentials and model routing rules (EXPAND)

Revision ID: arch22_step2_byok_credentials
Revises: arch22_step1_byok_vocabulary
Create Date: 2026-09-01

COLUMN SIZING FOR encrypted_api_key
===================================

app/core/encryption.py caps plaintext at 300 characters and ciphertext at 512.
Measured Fernet growth: a 300-character plaintext yields a 484-character
token, and the longest real provider key in circulation (an OpenAI `sk-proj-`
key, ~164 characters) yields 312. String(512) is therefore the exact ceiling
the encryption module already enforces, not a guess. Sizing it larger would
let a value in that `encrypt_password` would have refused, which is worse than
a tight column: the failure would land at read time instead of write time.

WHY THE PROVIDER COLUMN IS String + CHECK AND NOT THE ai_provider ENUM
======================================================================

`ai_provider` is a PostgreSQL enum holding {GROQ, GEMINI} and is owned by
workspace AI settings. Expanding it to six values would be a two-step
migration, would couple tenant credentials to a workspace-scoped concern, and
would make every future provider addition a schema change. ARCH-18 made the
same call for `cost_basis_source`. The vocabulary lives in
app/core/byok_providers.py and is mirrored here; verify_arch22.py G2 asserts
the two agree.

WHY THE UNIQUE INDEX IS PARTIAL
===============================

`uq_tenant_provider_credentials_org_provider_active` covers only rows where
is_active is true. A tenant may hold a history of deactivated credentials for
one provider — that is the rotation audit trail — but exactly one live key per
provider at a time. A full unique index would force a hard delete on rotation
and destroy the trail.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "arch22_step2_byok_credentials"
down_revision = "arch22_step1_byok_vocabulary"
branch_labels = None
depends_on = None


#: Mirrors app.core.byok_providers.BYOK_PROVIDER_VALUES. Duplicated here on
#: purpose: a migration must be readable and runnable years from now without
#: importing application code that may have moved. G2 in the gate asserts the
#: two lists are identical, so the duplication cannot drift silently.
BYOK_PROVIDER_VALUES: tuple[str, ...] = (
    "GROQ",
    "GEMINI",
    "OPENAI",
    "ANTHROPIC",
    "AZURE_OPENAI",
    "MISTRAL",
)

BYOK_TASK_TYPE_VALUES: tuple[str, ...] = (
    "ASSISTANT",
    "EXTRACTION",
    "SUMMARY",
    "VERIFICATION",
    "EMBEDDING",
)

MAX_CIPHERTEXT_LENGTH: int = 512

_PROVIDER_SQL = ", ".join(f"'{v}'" for v in BYOK_PROVIDER_VALUES)
_TASK_TYPE_SQL = ", ".join(f"'{v}'" for v in BYOK_TASK_TYPE_VALUES)


def upgrade() -> None:
    # -----------------------------------------------------------------------
    # 1. tenant_provider_credentials
    # -----------------------------------------------------------------------
    op.create_table(
        "tenant_provider_credentials",
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
            comment="Owning tenant. A credential is never resolved from any "
            "other context.",
        ),
        sa.Column(
            "provider",
            sa.String(length=32),
            nullable=False,
            comment="Provider vocabulary from app/core/byok_providers.py.",
        ),
        sa.Column(
            "encrypted_api_key",
            sa.String(length=MAX_CIPHERTEXT_LENGTH),
            nullable=False,
            comment="MultiFernet ciphertext. Never logged, never returned by "
            "any endpoint, never present in a response schema.",
        ),
        sa.Column(
            "key_version",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("1"),
            comment="Increments on every rotation. Lets an operator correlate "
            "a validation failure with a specific rotation event.",
        ),
        sa.Column(
            "key_fingerprint",
            sa.String(length=16),
            nullable=False,
            comment="First 12 hex chars of SHA-256 over the PLAINTEXT key. "
            "Lets a tenant confirm which key is loaded without the console "
            "ever receiving the key itself.",
        ),
        sa.Column(
            "key_last_four",
            sa.String(length=4),
            nullable=False,
            server_default=sa.text("''"),
            comment="Display-only tail, for the same reason as the "
            "fingerprint.",
        ),
        sa.Column(
            "is_active",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column(
            "allow_platform_fallback",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
            comment="OFF by default. Routing to the platform's own provider "
            "account without explicit consent is the behaviour BYOK exists to "
            "prevent; the safe value is therefore the default value.",
        ),
        sa.Column(
            "last_validated_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "last_validation_latency_ms",
            sa.Integer(),
            nullable=True,
        ),
        sa.Column(
            "validation_error",
            sa.String(length=512),
            nullable=True,
            comment="Provider-reported failure, truncated. NULL when the last "
            "validation succeeded.",
        ),
        sa.Column(
            "last_used_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "created_by_user_id",
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
        comment="ARCH-22 — tenant-supplied provider API keys, encrypted at rest.",
    )

    op.create_check_constraint(
        "ck_tenant_provider_credentials_provider_known",
        "tenant_provider_credentials",
        f"provider IN ({_PROVIDER_SQL})",
    )
    op.create_check_constraint(
        "ck_tenant_provider_credentials_key_version_positive",
        "tenant_provider_credentials",
        "key_version >= 1",
    )
    op.create_check_constraint(
        "ck_tenant_provider_credentials_ciphertext_present",
        "tenant_provider_credentials",
        "length(encrypted_api_key) > 0",
    )
    # A validated credential has a timestamp; an errored one has an error.
    # Without this an operator can find a row claiming both, or neither, and
    # the console badge becomes a coin flip.
    op.create_check_constraint(
        "ck_tenant_provider_credentials_validation_coherent",
        "tenant_provider_credentials",
        "(validation_error IS NULL) OR (last_validated_at IS NOT NULL)",
    )

    op.create_index(
        "uq_tenant_provider_credentials_org_provider_active",
        "tenant_provider_credentials",
        ["organization_id", "provider"],
        unique=True,
        postgresql_where=sa.text("is_active"),
    )
    op.create_index(
        "ix_tenant_provider_credentials_org",
        "tenant_provider_credentials",
        ["organization_id"],
    )

    # -----------------------------------------------------------------------
    # 2. tenant_model_routes
    # -----------------------------------------------------------------------
    op.create_table(
        "tenant_model_routes",
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
            "task_type",
            sa.String(length=32),
            nullable=False,
            comment="Pipeline stage this rule governs.",
        ),
        sa.Column(
            "provider",
            sa.String(length=32),
            nullable=False,
        ),
        sa.Column(
            "model_name",
            sa.String(length=128),
            nullable=False,
        ),
        sa.Column(
            "use_tenant_key",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
            comment="False routes this task to the platform account even when "
            "a tenant credential exists — a tenant may want BYOK for chat but "
            "not for background extraction.",
        ),
        sa.Column(
            "is_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
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
        comment="ARCH-22 — per-tenant routing of pipeline tasks to providers.",
    )

    op.create_check_constraint(
        "ck_tenant_model_routes_provider_known",
        "tenant_model_routes",
        f"provider IN ({_PROVIDER_SQL})",
    )
    op.create_check_constraint(
        "ck_tenant_model_routes_task_type_known",
        "tenant_model_routes",
        f"task_type IN ({_TASK_TYPE_SQL})",
    )
    op.create_check_constraint(
        "ck_tenant_model_routes_model_present",
        "tenant_model_routes",
        "length(btrim(model_name)) > 0",
    )

    # One rule per task per tenant. A second rule for ASSISTANT would make
    # resolution order-dependent, and "whichever row the planner returned
    # first" is not a routing policy.
    op.create_index(
        "uq_tenant_model_routes_org_task",
        "tenant_model_routes",
        ["organization_id", "task_type"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("uq_tenant_model_routes_org_task", table_name="tenant_model_routes")
    op.drop_table("tenant_model_routes")

    op.drop_index(
        "ix_tenant_provider_credentials_org",
        table_name="tenant_provider_credentials",
    )
    op.drop_index(
        "uq_tenant_provider_credentials_org_provider_active",
        table_name="tenant_provider_credentials",
    )
    op.drop_table("tenant_provider_credentials")

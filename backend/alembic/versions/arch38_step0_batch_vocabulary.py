"""ARCH-38 Step 0 — audit vocabulary for batch ingestion (EXPAND)

Revision ID: arch38_step0_batch_vocabulary
Revises: arch37_step1_flow_builder
Create Date: 2026-09-17

WHY THIS IS ITS OWN MIGRATION
=============================

`arch38_step1_batches` seeds the platform preset packs and writes one audit row
per seeded preset. Those rows need the resource type DOCUMENT_SCHEMA_PRESET,
and PostgreSQL refuses to let an enum value be used in the transaction that
added it. ARCH-20, 22, 26, 31 and 37 split vocabulary from DDL for the same
reason; this follows them.

INGESTION_BATCH is what the bulk endpoint records against, and RETENTION_HOLD
is what placing or releasing a hold records. A bulk delete of two hundred
documents that wrote no audit row would be the single most consequential
unaudited action in the product.
"""

from __future__ import annotations

from alembic import op

revision = "arch38_step0_batch_vocabulary"
down_revision = "arch37_step1_flow_builder"
branch_labels = None
depends_on = None

NEW_RESOURCE_TYPES: tuple[str, ...] = (
    "INGESTION_BATCH",
    "DOCUMENT_SCHEMA_PRESET",
    "RETENTION_HOLD",
)


def upgrade() -> None:
    with op.get_context().autocommit_block():
        for value in NEW_RESOURCE_TYPES:
            op.execute(
                f"ALTER TYPE audit_resource_type ADD VALUE IF NOT EXISTS '{value}'"
            )


def downgrade() -> None:
    """No-op, deliberately.

    PostgreSQL cannot drop an enum value, and rewriting `audit_resource_type`
    would rewrite every audit row, which ARCH-07's immutability trigger
    refuses. A value nothing emits is harmless.
    """

"""PHASE 5 (F-017) — bring two database definitions in line with the models.

Revision ID: p5a1_schema_drift_alignment
Revises: p4a3_payment_risk_flags
Create Date: 2026-10-06

The model/migration drift gate (scripts/check_migration_drift.py) listed 22
real differences between the database the migrations build and the models.
Twenty are fixed in the models (names, types and nullability the database
already had). These two are fixed here, because the model is the correct side:

1. `automation_rules.conditions` / `.actions` are JSON in the database but
   JSONB in the model, so a JSONB operator the ORM emits for them would fail at
   run time. JSON -> JSONB is lossless for the values stored (lists of objects).

2. `uploaded_files.owner_id` cascaded on user deletion. Every document upload
   records its uploader as the owner, so deleting a user row would have deleted
   the file records of every document that person ever uploaded for the
   company (work items keep only a SET NULL pointer to them). The model always
   said SET NULL: the company's files outlive the person who uploaded them.
   The application never hard-deletes users (erasure anonymises), so this only
   changes what a manual DELETE does.

Both steps are reversible.
"""

from __future__ import annotations

from alembic import op

revision = "p5a1_schema_drift_alignment"
down_revision = "p4a3_payment_risk_flags"
branch_labels = None
depends_on = None

FK = "fk_uploaded_files_owner_id_users"


def upgrade() -> None:
    op.execute(
        "ALTER TABLE automation_rules "
        "ALTER COLUMN conditions TYPE jsonb USING conditions::jsonb, "
        "ALTER COLUMN actions TYPE jsonb USING actions::jsonb"
    )

    op.drop_constraint(FK, "uploaded_files", type_="foreignkey")
    op.create_foreign_key(
        FK, "uploaded_files", "users", ["owner_id"], ["id"], ondelete="SET NULL"
    )


def downgrade() -> None:
    op.drop_constraint(FK, "uploaded_files", type_="foreignkey")
    op.create_foreign_key(
        FK, "uploaded_files", "users", ["owner_id"], ["id"], ondelete="CASCADE"
    )

    op.execute(
        "ALTER TABLE automation_rules "
        "ALTER COLUMN conditions TYPE json USING conditions::json, "
        "ALTER COLUMN actions TYPE json USING actions::json"
    )

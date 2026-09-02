"""arch08_step5_audit_outcome_contract

ARCH-08 Step 5 — MIGRATE & CONTRACT. Backfills historical DENIED outcomes,
tightens outcome to NOT NULL, creates partial index for denials, drops redundant index,
and remediates legacy uploaded_files file_path prefixes.
"""

from typing import Sequence, Union
import sqlalchemy as sa
from alembic import op

revision: str = "arch08_step5_outcome_contract"
down_revision: Union[str, None] = "arch08_step4_outcome_expand"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

EXEMPT_TRIGGER_FUNCTION = """
CREATE OR REPLACE FUNCTION fn_audit_logs_prevent_mutation()
RETURNS TRIGGER LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    sweeper_role  text := current_setting('flowpilot.audit_sweeper_role', true);
    backfill_tag  text := current_setting('flowpilot.audit_backfill', true);
BEGIN
    IF (TG_OP = 'UPDATE') THEN
        IF backfill_tag = 'arch08_step5'
           AND OLD.outcome = 'ALLOWED'::audit_outcome
           AND NEW.outcome = 'DENIED'::audit_outcome
           AND OLD.details ->> 'outcome' = 'DENIED'
           AND (to_jsonb(NEW) - 'outcome') = (to_jsonb(OLD) - 'outcome')
        THEN
            RETURN NEW;
        END IF;

        RAISE EXCEPTION
            'audit_logs is append-only: UPDATE is not permitted (row id=%)', OLD.id
            USING ERRCODE = 'AU001',
                  HINT = 'Corrections are recorded as new audit rows, never as edits to existing ones.';
    END IF;

    IF (TG_OP = 'DELETE') THEN
        IF sweeper_role IS NOT NULL AND session_user = sweeper_role THEN
            IF OLD.created_at >= (now() - interval '400 days') THEN
                RAISE EXCEPTION
                    'The retention sweeper may only delete audit rows older than 400 days (row created_at=%)', OLD.created_at
                    USING ERRCODE = 'AU002';
            END IF;
            RETURN OLD;
        END IF;

        RAISE EXCEPTION
            'audit_logs is append-only: DELETE is not permitted. Only the retention sweeper role may delete rows older than 400 days.'
            USING ERRCODE = 'AU001';
    END IF;

    RETURN NULL;
END;
$$;
"""

RESTORE_TRIGGER_FUNCTION = """
CREATE OR REPLACE FUNCTION fn_audit_logs_prevent_mutation()
RETURNS TRIGGER LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    sweeper_role  text := current_setting('flowpilot.audit_sweeper_role', true);
BEGIN
    IF (TG_OP = 'UPDATE') THEN
        RAISE EXCEPTION
            'audit_logs is append-only: UPDATE is not permitted (row id=%)', OLD.id
            USING ERRCODE = 'AU001',
                  HINT = 'Corrections are recorded as new audit rows, never as edits to existing ones.';
    END IF;

    IF (TG_OP = 'DELETE') THEN
        IF sweeper_role IS NOT NULL AND session_user = sweeper_role THEN
            IF OLD.created_at >= (now() - interval '400 days') THEN
                RAISE EXCEPTION
                    'The retention sweeper may only delete audit rows older than 400 days (row created_at=%)', OLD.created_at
                    USING ERRCODE = 'AU002';
            END IF;
            RETURN OLD;
        END IF;

        RAISE EXCEPTION
            'audit_logs is append-only: DELETE is not permitted. Only the retention sweeper role may delete rows older than 400 days.'
            USING ERRCODE = 'AU001';
    END IF;

    RETURN NULL;
END;
$$;
"""


def upgrade() -> None:
    bind = op.get_bind()

    # 1. Install trigger exemption function and enable transaction-local backfill GUC
    op.execute(EXEMPT_TRIGGER_FUNCTION)
    bind.execute(sa.text("SELECT set_config('flowpilot.audit_backfill', 'arch08_step5', true)"))

    # 2. Backfill pre-existing DENIED rows
    bind.execute(sa.text(
        "UPDATE audit_logs SET outcome = 'DENIED'::audit_outcome "
        "WHERE details ->> 'outcome' = 'DENIED' AND outcome = 'ALLOWED'::audit_outcome"
    ))

    # 3. Restore unconditional immutability trigger function
    op.execute(RESTORE_TRIGGER_FUNCTION)

    # 4. CONTRACT: SET NOT NULL via non-blocking constraint validation
    op.execute(
        "ALTER TABLE audit_logs ADD CONSTRAINT ck_audit_logs_outcome_not_null "
        "CHECK (outcome IS NOT NULL) NOT VALID"
    )
    op.execute("ALTER TABLE audit_logs VALIDATE CONSTRAINT ck_audit_logs_outcome_not_null")
    op.alter_column("audit_logs", "outcome", nullable=False)
    op.execute("ALTER TABLE audit_logs DROP CONSTRAINT ck_audit_logs_outcome_not_null")

    # 5. Index changes (partial denial index + drop redundant legacy index)
    with op.get_context().autocommit_block():
        op.create_index(
            "ix_audit_logs_denied_organization_id_created_at",
            "audit_logs",
            ["organization_id", sa.text("created_at DESC")],
            postgresql_where=sa.text("outcome = 'DENIED'::audit_outcome"),
            postgresql_concurrently=True,
        )
        op.drop_index(
            "ix_audit_logs_organization_id_created_at",
            table_name="audit_logs",
            postgresql_concurrently=True,
        )

    # 6. Step 1 Logo Remediation: strip /uploads/ prefix from uploaded_files.file_path
    bind.execute(sa.text(
        "UPDATE uploaded_files "
        "SET file_path = regexp_replace(file_path, '^/?uploads/', '') "
        "WHERE file_path ~ '^/?uploads/'"
    ))


def downgrade() -> None:
    op.alter_column("audit_logs", "outcome", nullable=True)

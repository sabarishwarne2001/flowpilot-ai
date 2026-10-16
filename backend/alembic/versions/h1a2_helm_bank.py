"""Helm: bank statement import (owner addition A, flagship session).

  helm_bank_accounts   a bank account a book imports statements for: a name, the last four digits
                       only (never the full number), its currency, and the column mapping its
                       bank's exports use (saved when a person confirms it)
  helm_bank_imports    one imported file: who, when, its fingerprint (the same file twice adds
                       nothing), its period, its closing balance
  helm_bank_lines      one statement line: date, narration, reference, signed amount, running
                       balance; a fingerprint per account so an overlapping period adds no line twice;
                       whether it is matched to an invoice (automatically or by a person), suggested,
                       ignored or waiting for a person. Deleting the matched invoice clears only
                       work_item_id (ON DELETE SET NULL (work_item_id), Postgres 15 and later): the line
                       keeps its workspace, so it stays masked to people who cannot read that workspace

helm_cash_positions gains source BANK (a bank import's closing balance, tied to its import and
account), and audit_resource_type gains HELM_BANK. Expand only; the downgrade drops what this adds.

Revision ID: h1a2_helm_bank
Revises: h1a1_helm
"""

from __future__ import annotations

from alembic import op

revision = "h1a2_helm_bank"
down_revision = "h1a1_helm"
branch_labels = None
depends_on = None

TS = "timestamptz NOT NULL DEFAULT now()"
ORG = "organization_id uuid NOT NULL REFERENCES organizations (id) ON DELETE CASCADE"
USER = "uuid REFERENCES users (id) ON DELETE SET NULL"
BOOK_FK = ("CONSTRAINT fk_{table}_book_org FOREIGN KEY (book_id, organization_id) "
           "REFERENCES helm_books (id, organization_id) ON DELETE CASCADE")
ACCOUNT_FK = ("CONSTRAINT fk_{table}_account_org FOREIGN KEY (bank_account_id, organization_id) "
              "REFERENCES helm_bank_accounts (id, organization_id) ON DELETE CASCADE")


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE audit_resource_type ADD VALUE IF NOT EXISTS 'HELM_BANK'")

    op.execute(
        f"""
        CREATE TABLE helm_bank_accounts (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            {ORG},
            book_id uuid NOT NULL,
            name varchar(120) NOT NULL,
            last4 varchar(4),
            currency varchar(3) NOT NULL,
            column_mapping jsonb,
            created_by_user_id {USER},
            created_at {TS},
            updated_at {TS},
            {BOOK_FK.format(table="helm_bank_accounts")},
            CONSTRAINT uq_helm_bank_accounts_id_org UNIQUE (id, organization_id),
            CONSTRAINT uq_helm_bank_accounts_book_name UNIQUE (book_id, name),
            CONSTRAINT ck_helm_bank_accounts_currency CHECK (currency ~ '^[A-Z]{{3}}$'),
            CONSTRAINT ck_helm_bank_accounts_last4 CHECK (last4 IS NULL OR last4 ~ '^[0-9A-Za-z]{{1,4}}$')
        )
        """
    )

    op.execute(
        f"""
        CREATE TABLE helm_bank_imports (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            {ORG},
            book_id uuid NOT NULL,
            bank_account_id uuid NOT NULL,
            filename varchar(255) NOT NULL,
            sha256 varchar(64) NOT NULL,
            lines_read integer NOT NULL DEFAULT 0,
            lines_new integer NOT NULL DEFAULT 0,
            lines_matched integer NOT NULL DEFAULT 0,
            problems jsonb NOT NULL DEFAULT '[]'::jsonb,
            period_start date,
            period_end date,
            closing_balance_micros bigint,
            closing_on date,
            imported_by_user_id {USER},
            created_at {TS},
            {BOOK_FK.format(table="helm_bank_imports")},
            {ACCOUNT_FK.format(table="helm_bank_imports")},
            CONSTRAINT uq_helm_bank_imports_id_org UNIQUE (id, organization_id),
            CONSTRAINT uq_helm_bank_imports_account_file UNIQUE (bank_account_id, sha256)
        )
        """
    )

    op.execute(
        f"""
        CREATE TABLE helm_bank_lines (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            {ORG},
            book_id uuid NOT NULL,
            bank_account_id uuid NOT NULL,
            import_id uuid NOT NULL,
            line_no integer NOT NULL,
            on_date date NOT NULL,
            description varchar(500) NOT NULL DEFAULT '',
            reference varchar(120) NOT NULL DEFAULT '',
            amount_micros bigint NOT NULL,
            balance_micros bigint,
            currency varchar(3) NOT NULL,
            fingerprint varchar(64) NOT NULL,
            status varchar(10) NOT NULL DEFAULT 'UNMATCHED',
            workspace_id uuid,
            work_item_id uuid,
            action_id uuid,
            match jsonb NOT NULL DEFAULT '{{}}'::jsonb,
            note varchar(300),
            decided_by_user_id {USER},
            decided_at timestamptz,
            created_at {TS},
            {BOOK_FK.format(table="helm_bank_lines")},
            {ACCOUNT_FK.format(table="helm_bank_lines")},
            CONSTRAINT fk_helm_bank_lines_import_org FOREIGN KEY (import_id, organization_id)
                REFERENCES helm_bank_imports (id, organization_id) ON DELETE CASCADE,
            CONSTRAINT fk_helm_bank_lines_workspace_org FOREIGN KEY (workspace_id, organization_id)
                REFERENCES workspaces (id, organization_id) ON DELETE CASCADE,
            CONSTRAINT fk_helm_bank_lines_work_item_workspace FOREIGN KEY (work_item_id, workspace_id)
                REFERENCES work_items (id, workspace_id) ON DELETE SET NULL (work_item_id),
            CONSTRAINT uq_helm_bank_lines_account_fingerprint UNIQUE (bank_account_id, fingerprint),
            CONSTRAINT ck_helm_bank_lines_status CHECK (status IN ('UNMATCHED', 'SUGGESTED', 'MATCHED', 'IGNORED')),
            CONSTRAINT ck_helm_bank_lines_amount CHECK (amount_micros <> 0)
        )
        """
    )
    op.execute("CREATE INDEX ix_helm_bank_lines_book_status ON helm_bank_lines (book_id, status, on_date)")
    op.execute("CREATE INDEX ix_helm_bank_lines_work_item ON helm_bank_lines (work_item_id)")

    op.execute("ALTER TABLE helm_cash_positions ADD COLUMN bank_account_id uuid")
    op.execute("ALTER TABLE helm_cash_positions ADD COLUMN bank_import_id uuid")
    op.execute(
        "ALTER TABLE helm_cash_positions ADD CONSTRAINT fk_helm_cash_positions_account_org "
        "FOREIGN KEY (bank_account_id, organization_id) REFERENCES helm_bank_accounts (id, organization_id) "
        "ON DELETE CASCADE"
    )
    op.execute(
        "ALTER TABLE helm_cash_positions ADD CONSTRAINT fk_helm_cash_positions_import_org "
        "FOREIGN KEY (bank_import_id, organization_id) REFERENCES helm_bank_imports (id, organization_id) "
        "ON DELETE CASCADE"
    )
    op.execute("ALTER TABLE helm_cash_positions DROP CONSTRAINT ck_helm_cash_positions_source")
    op.execute("ALTER TABLE helm_cash_positions ADD CONSTRAINT ck_helm_cash_positions_source "
               "CHECK (source IN ('STATEMENT', 'MANUAL', 'BANK'))")
    op.execute("ALTER TABLE helm_cash_positions ADD CONSTRAINT ck_helm_cash_positions_bank_has_import "
               "CHECK (source <> 'BANK' OR (bank_import_id IS NOT NULL AND bank_account_id IS NOT NULL))")


def downgrade() -> None:
    op.execute("DELETE FROM helm_cash_positions WHERE source = 'BANK'")
    op.execute("ALTER TABLE helm_cash_positions DROP CONSTRAINT ck_helm_cash_positions_bank_has_import")
    op.execute("ALTER TABLE helm_cash_positions DROP CONSTRAINT ck_helm_cash_positions_source")
    op.execute("ALTER TABLE helm_cash_positions ADD CONSTRAINT ck_helm_cash_positions_source "
               "CHECK (source IN ('STATEMENT', 'MANUAL'))")
    op.execute("ALTER TABLE helm_cash_positions DROP CONSTRAINT fk_helm_cash_positions_import_org")
    op.execute("ALTER TABLE helm_cash_positions DROP CONSTRAINT fk_helm_cash_positions_account_org")
    op.execute("ALTER TABLE helm_cash_positions DROP COLUMN bank_import_id")
    op.execute("ALTER TABLE helm_cash_positions DROP COLUMN bank_account_id")
    op.execute("DROP TABLE IF EXISTS helm_bank_lines")
    op.execute("DROP TABLE IF EXISTS helm_bank_imports")
    op.execute("DROP TABLE IF EXISTS helm_bank_accounts")
    # audit_resource_type keeps HELM_BANK: Postgres cannot drop an enum value, and audit rows may use it.

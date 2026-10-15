"""Flagship session — Helm: outcome control (the Promise ledger, mandates, plans, the Guard).

Revision ID: h1a1_helm
Revises: s4b1_drop_email_settings
Create Date: 2026-10-14

EXPAND ONLY. Fourteen new tables, one new column, two widened vocabularies, one new review-hub
branch. Nothing existing is altered beyond that, so a downgrade loses only Helm's own rows.

WHAT EACH TABLE IS FOR (docs/helm/ARCHITECTURE.md §3)
=====================================================

  helm_books            one company Helm runs: a workspace, every workspace (Enterprise), the sample
  helm_settings         a book's identities, payment-run days, horizon, cost of capital, first run
  helm_counterparties   a book's suppliers and customers: MSME status, contact, behaviour
  helm_settlements      whether an invoice was paid, held or disputed, with evidence (source of truth)
  helm_cash_positions   cash per currency on a date (a statement's balance, or typed)
  helm_promises         the Promise ledger (a rebuildable projection; deterministic source keys)
  helm_mandates         a mandate; its spec lives in ...
  helm_mandate_versions ... immutable versions (UPDATE refused by trigger)
  helm_plans            versioned plans, superseded never edited (one CURRENT per book)
  helm_actions          every proposal and execution, with its Guard result and idempotency key
  helm_certificates     the canonical record of a Guard decision and its SHA-256 (UPDATE refused)
  helm_backtests        Time-machine runs
  helm_value_events     evidence-backed value held, counted once (unique dedupe key)
  helm_tasks            tasks Helm opens for people; the review hub lists them as kind HELM

TENANT ISOLATION IS DECLARATIVE
===============================

The ARCH-41 pattern: `(workspace_id, organization_id) REFERENCES workspaces (id, organization_id)`,
`(work_item_id, workspace_id) REFERENCES work_items (id, workspace_id)` and
`(book_id, organization_id) REFERENCES helm_books (id, organization_id)`. A row naming another
tenant's document or book is refused by PostgreSQL with 23503.

`workspaces.is_sample` marks "Explore Helm with a sample company"'s workspace.

The review hub's view becomes v11: v10 (s4a3) plus a HELM branch over helm_tasks, and
review_assignments accepts kind HELM. `audit_resource_type` and `audit_action` gain Helm's values
(PostgreSQL cannot drop an enum value, so the downgrade leaves them, as every vocabulary migration
before this one does).
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

from alembic import op

revision = "h1a1_helm"
down_revision = "s4b1_drop_email_settings"
branch_labels = None
depends_on = None

TS = "timestamptz NOT NULL DEFAULT now()"

NEW_AUDIT_RESOURCE_TYPES = (
    "HELM_BOOK",
    "HELM_SETTINGS",
    "HELM_MANDATE",
    "HELM_PLAN",
    "HELM_ACTION",
    "HELM_BACKTEST",
    "HELM_TASK",
)
NEW_AUDIT_ACTIONS = ("APPROVED", "REJECTED", "EXECUTED", "UNDONE", "BLOCKED")

TABLES_IN_DROP_ORDER = (
    "helm_tasks",
    "helm_value_events",
    "helm_backtests",
    "helm_certificates",
    "helm_actions",
    "helm_plans",
    "helm_mandate_versions",
    "helm_mandates",
    "helm_promises",
    "helm_cash_positions",
    "helm_settlements",
    "helm_counterparties",
    "helm_settings",
    "helm_books",
)

REVIEW_KINDS_BEFORE = (
    "EXTRACTION", "ASSERTION", "ANOMALY", "MERGE", "SPLIT", "TABLE", "CORROBORATION", "OBLIGATION", "POSTING",
)
REVIEW_KINDS_AFTER = REVIEW_KINDS_BEFORE + ("HELM",)

_HELM_BRANCH = """
    UNION ALL

    SELECT
        'HELM'::varchar(16),
        ht.id,
        ht.organization_id,
        ht.workspace_id,
        ht.work_item_id,
        ('Helm: ' || ht.title)::text,
        ht.severity::varchar(8),
        CASE ht.severity WHEN 'HIGH' THEN 2 WHEN 'MEDIUM' THEN 3 ELSE 4 END::integer,
        NULL::numeric(5, 4),
        ht.created_at,
        CASE WHEN ht.status = 'OPEN' THEN 'OPEN' ELSE 'RESOLVED' END::varchar(8),
        ht.resolved_at,
        ht.resolved_by_user_id,
        'HELM_TASK'::varchar(24)
    FROM helm_tasks ht
"""


def _v10() -> str:
    path = Path(__file__).with_name("s4a3_review_view_drops_deleted_checks.py")
    spec = importlib.util.spec_from_file_location("s4a3_review_view_drops_deleted_checks", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.review_queue_view_v10()


def review_queue_view_v11() -> str:
    return _v10().rstrip() + "\n" + _HELM_BRANCH


def _review_kind_check(kinds: tuple[str, ...]) -> None:
    op.execute("ALTER TABLE review_assignments DROP CONSTRAINT IF EXISTS ck_review_assignments_kind_known")
    values = ", ".join(f"'{kind}'" for kind in kinds)
    op.execute(f"ALTER TABLE review_assignments ADD CONSTRAINT ck_review_assignments_kind_known CHECK (kind IN ({values}))")


def _in(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def upgrade() -> None:
    # Enum values first: ADD VALUE cannot run inside a transaction block. IF NOT EXISTS keeps a
    # re-run after a partial failure safe.
    with op.get_context().autocommit_block():
        for value in NEW_AUDIT_RESOURCE_TYPES:
            op.execute(f"ALTER TYPE audit_resource_type ADD VALUE IF NOT EXISTS '{value}'")
        for value in NEW_AUDIT_ACTIONS:
            op.execute(f"ALTER TYPE audit_action ADD VALUE IF NOT EXISTS '{value}'")

    op.execute("ALTER TABLE workspaces ADD COLUMN is_sample boolean NOT NULL DEFAULT false")
    # `/:orgSlug/helm` is Helm's address: a workspace already slugged "helm" would be shadowed by it.
    # Give it a free slug (app/core/slugs.py now reserves "helm" for new ones). Not reversed.
    op.execute("UPDATE workspaces SET slug = 'helm-' || left(md5(id::text), 6) WHERE lower(slug) = 'helm'")

    op.execute(
        """
        CREATE FUNCTION helm_refuse_update() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'Helm % rows are append-only: % cannot be updated', TG_TABLE_NAME, OLD
                USING ERRCODE = 'check_violation';
        END;
        $$
        """
    )

    op.execute(
        f"""
        CREATE TABLE helm_books (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id uuid NOT NULL REFERENCES organizations (id) ON DELETE CASCADE,
            workspace_id uuid,
            kind varchar(12) NOT NULL,
            name varchar(200) NOT NULL,
            status varchar(10) NOT NULL DEFAULT 'ACTIVE',
            created_by_user_id uuid REFERENCES users (id) ON DELETE SET NULL,
            created_at {TS},
            updated_at {TS},
            CONSTRAINT uq_helm_books_id_org UNIQUE (id, organization_id),
            CONSTRAINT fk_helm_books_workspace_org FOREIGN KEY (workspace_id, organization_id)
                REFERENCES workspaces (id, organization_id) ON DELETE CASCADE,
            CONSTRAINT ck_helm_books_kind CHECK (kind IN ('WORKSPACE', 'ORGANIZATION', 'SAMPLE')),
            CONSTRAINT ck_helm_books_status CHECK (status IN ('ACTIVE', 'ARCHIVED')),
            CONSTRAINT ck_helm_books_scope CHECK ((kind = 'ORGANIZATION') = (workspace_id IS NULL))
        )
        """
    )
    op.execute("CREATE INDEX ix_helm_books_organization ON helm_books (organization_id)")
    op.execute(
        "CREATE UNIQUE INDEX uq_helm_books_live_workspace ON helm_books (workspace_id) "
        "WHERE workspace_id IS NOT NULL AND status = 'ACTIVE'"
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_helm_books_live_organization ON helm_books (organization_id) "
        "WHERE kind = 'ORGANIZATION' AND status = 'ACTIVE'"
    )

    book_fk = (
        "CONSTRAINT fk_{table}_book_org FOREIGN KEY (book_id, organization_id) "
        "REFERENCES helm_books (id, organization_id) ON DELETE CASCADE"
    )
    workspace_fk = (
        "CONSTRAINT fk_{table}_workspace_org FOREIGN KEY (workspace_id, organization_id) "
        "REFERENCES workspaces (id, organization_id) ON DELETE CASCADE"
    )
    work_item_fk = (
        "CONSTRAINT fk_{table}_work_item_workspace FOREIGN KEY (work_item_id, workspace_id) "
        "REFERENCES work_items (id, workspace_id) ON DELETE CASCADE"
    )
    org = "organization_id uuid NOT NULL REFERENCES organizations (id) ON DELETE CASCADE"
    user = "uuid REFERENCES users (id) ON DELETE SET NULL"

    op.execute(
        f"""
        CREATE TABLE helm_settings (
            book_id uuid PRIMARY KEY,
            {org},
            identities jsonb NOT NULL DEFAULT '[]'::jsonb,
            identities_confirmed_at timestamptz,
            identities_confirmed_by_user_id {user},
            run_weekdays smallint[] NOT NULL DEFAULT '{{2,5}}',
            horizon_days smallint NOT NULL DEFAULT 30,
            cost_of_capital_bps integer NOT NULL DEFAULT 1200,
            default_autonomy varchar(8) NOT NULL DEFAULT 'propose',
            briefing_enabled boolean NOT NULL DEFAULT true,
            business_type varchar(32),
            first_run_step smallint NOT NULL DEFAULT 0,
            first_run_completed_at timestamptz,
            preferences jsonb NOT NULL DEFAULT '{{}}'::jsonb,
            updated_by_user_id {user},
            created_at {TS},
            updated_at {TS},
            {book_fk.format(table="helm_settings")},
            CONSTRAINT ck_helm_settings_horizon CHECK (horizon_days BETWEEN 7 AND 90),
            CONSTRAINT ck_helm_settings_cost_of_capital CHECK (cost_of_capital_bps BETWEEN 0 AND 10000),
            CONSTRAINT ck_helm_settings_autonomy CHECK (default_autonomy IN ('watch', 'propose', 'act')),
            CONSTRAINT ck_helm_settings_identities_array CHECK (jsonb_typeof(identities) = 'array')
        )
        """
    )

    op.execute(
        f"""
        CREATE TABLE helm_counterparties (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            {org},
            book_id uuid NOT NULL,
            key varchar(255) NOT NULL,
            role varchar(10) NOT NULL,
            display_name varchar(300) NOT NULL,
            tax_id varchar(64),
            entity_ids uuid[] NOT NULL DEFAULT '{{}}',
            udyam_number varchar(32),
            msme_category varchar(8),
            msme_source varchar(12),
            msme_confirmed_by_user_id {user},
            msme_confirmed_at timestamptz,
            contact_email varchar(320),
            contact_name varchar(200),
            behaviour jsonb NOT NULL DEFAULT '{{}}'::jsonb,
            created_at {TS},
            updated_at {TS},
            CONSTRAINT uq_helm_counterparties_book_key UNIQUE (book_id, key),
            {book_fk.format(table="helm_counterparties")},
            CONSTRAINT ck_helm_counterparties_role CHECK (role IN ('SUPPLIER', 'CUSTOMER', 'BOTH')),
            CONSTRAINT ck_helm_counterparties_msme_category CHECK
                (msme_category IS NULL OR msme_category IN ('MICRO', 'SMALL', 'MEDIUM', 'NONE')),
            CONSTRAINT ck_helm_counterparties_msme_source CHECK
                (msme_source IS NULL OR msme_source IN ('DOCUMENT', 'CERTIFICATE', 'PERSON'))
        )
        """
    )

    op.execute(
        f"""
        CREATE TABLE helm_settlements (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            {org},
            workspace_id uuid NOT NULL,
            work_item_id uuid NOT NULL,
            state varchar(10) NOT NULL,
            run_date date,
            paid_on date,
            paid_amount_micros bigint,
            paid_reference varchar(200),
            hold_reason text,
            source varchar(10) NOT NULL,
            evidence jsonb NOT NULL DEFAULT '{{}}'::jsonb,
            action_id uuid,
            version integer NOT NULL DEFAULT 1,
            updated_by_user_id {user},
            created_at {TS},
            updated_at {TS},
            CONSTRAINT uq_helm_settlements_work_item UNIQUE (work_item_id),
            {workspace_fk.format(table="helm_settlements")},
            {work_item_fk.format(table="helm_settlements")},
            CONSTRAINT ck_helm_settlements_state CHECK
                (state IN ('UNPAID', 'SCHEDULED', 'APPROVED', 'PAID', 'HELD', 'DISPUTED')),
            CONSTRAINT ck_helm_settlements_source CHECK (source IN ('PERSON', 'STATEMENT', 'HELM')),
            CONSTRAINT ck_helm_settlements_paid_has_date CHECK (state <> 'PAID' OR paid_on IS NOT NULL),
            CONSTRAINT ck_helm_settlements_scheduled_has_date CHECK
                (state NOT IN ('SCHEDULED', 'APPROVED') OR run_date IS NOT NULL)
        )
        """
    )
    op.execute("CREATE INDEX ix_helm_settlements_workspace_state ON helm_settlements (workspace_id, state)")

    op.execute(
        f"""
        CREATE TABLE helm_cash_positions (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            {org},
            book_id uuid NOT NULL,
            currency varchar(3) NOT NULL,
            amount_micros bigint NOT NULL,
            as_of date NOT NULL,
            source varchar(10) NOT NULL,
            workspace_id uuid,
            work_item_id uuid,
            note varchar(300),
            entered_by_user_id {user},
            created_at {TS},
            {book_fk.format(table="helm_cash_positions")},
            {work_item_fk.format(table="helm_cash_positions")},
            CONSTRAINT ck_helm_cash_positions_source CHECK (source IN ('STATEMENT', 'MANUAL')),
            CONSTRAINT ck_helm_cash_positions_currency CHECK (currency ~ '^[A-Z]{{3}}$'),
            CONSTRAINT ck_helm_cash_positions_statement_has_document CHECK
                (source <> 'STATEMENT' OR work_item_id IS NOT NULL)
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_helm_cash_positions_book_currency ON helm_cash_positions (book_id, currency, as_of)"
    )

    op.execute(
        f"""
        CREATE TABLE helm_promises (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            {org},
            workspace_id uuid NOT NULL,
            work_item_id uuid,
            source_key varchar(64) NOT NULL,
            kind varchar(24) NOT NULL,
            direction varchar(12) NOT NULL,
            counterparty_key varchar(255),
            counterparty_name varchar(300),
            currency varchar(3),
            amount_micros bigint,
            due_date date,
            window_end date,
            status varchar(10) NOT NULL,
            confidence varchar(10) NOT NULL,
            title text NOT NULL,
            terms jsonb NOT NULL DEFAULT '{{}}'::jsonb,
            evidence jsonb NOT NULL DEFAULT '[]'::jsonb,
            fulfilments jsonb NOT NULL DEFAULT '[]'::jsonb,
            details jsonb NOT NULL DEFAULT '{{}}'::jsonb,
            obligation_id uuid,
            engine_version varchar(16) NOT NULL,
            content_hash varchar(64) NOT NULL,
            created_at {TS},
            updated_at {TS},
            CONSTRAINT uq_helm_promises_source_key UNIQUE (source_key),
            {workspace_fk.format(table="helm_promises")},
            {work_item_fk.format(table="helm_promises")},
            CONSTRAINT ck_helm_promises_kind CHECK (kind IN ('PAYMENT_DUE', 'PAYMENT_EXPECTED', 'PRICE_AGREED',
                'DELIVERY_DUE', 'DISCOUNT_OFFERED', 'NOTICE_DEADLINE', 'DOCUMENT_VALID_UNTIL', 'OBLIGATION')),
            CONSTRAINT ck_helm_promises_direction CHECK
                (direction IN ('WE_OWE', 'OWED_TO_US', 'OPTION', 'OUR_DECISION')),
            CONSTRAINT ck_helm_promises_status CHECK
                (status IN ('OPEN', 'PARTIAL', 'KEPT', 'BROKEN', 'WAIVED', 'EXPIRED')),
            CONSTRAINT ck_helm_promises_confidence CHECK (confidence IN ('VERIFIED', 'CALIBRATED', 'EXTRACTED')),
            CONSTRAINT ck_helm_promises_currency CHECK (currency IS NULL OR currency ~ '^[A-Z]{{3}}$')
        )
        """
    )
    op.execute("CREATE INDEX ix_helm_promises_workspace_kind_status ON helm_promises (workspace_id, kind, status)")
    op.execute("CREATE INDEX ix_helm_promises_work_item ON helm_promises (work_item_id)")
    op.execute("CREATE INDEX ix_helm_promises_counterparty ON helm_promises (workspace_id, counterparty_key)")

    op.execute(
        f"""
        CREATE TABLE helm_mandates (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            {org},
            book_id uuid NOT NULL,
            template varchar(48) NOT NULL,
            status varchar(10) NOT NULL,
            current_version integer NOT NULL DEFAULT 1,
            title varchar(200) NOT NULL,
            created_by_user_id {user},
            created_at {TS},
            updated_at {TS},
            activated_at timestamptz,
            CONSTRAINT uq_helm_mandates_id_org UNIQUE (id, organization_id),
            {book_fk.format(table="helm_mandates")},
            CONSTRAINT ck_helm_mandates_status CHECK (status IN ('DRAFT', 'ACTIVE', 'PAUSED', 'ARCHIVED')),
            CONSTRAINT ck_helm_mandates_version CHECK (current_version >= 1)
        )
        """
    )
    op.execute("CREATE INDEX ix_helm_mandates_book_status ON helm_mandates (book_id, status)")

    op.execute(
        f"""
        CREATE TABLE helm_mandate_versions (
            mandate_id uuid NOT NULL REFERENCES helm_mandates (id) ON DELETE CASCADE,
            version integer NOT NULL,
            {org},
            spec jsonb NOT NULL,
            spec_hash varchar(64) NOT NULL,
            readback text NOT NULL,
            created_by_user_id {user},
            created_at {TS},
            PRIMARY KEY (mandate_id, version),
            CONSTRAINT ck_helm_mandate_versions_spec_object CHECK (jsonb_typeof(spec) = 'object'),
            CONSTRAINT ck_helm_mandate_versions_hash CHECK (spec_hash ~ '^[0-9a-f]{{64}}$')
        )
        """
    )
    op.execute(
        "CREATE TRIGGER trg_helm_mandate_versions_append_only BEFORE UPDATE ON helm_mandate_versions "
        "FOR EACH ROW EXECUTE FUNCTION helm_refuse_update()"
    )

    op.execute(
        f"""
        CREATE TABLE helm_plans (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            {org},
            book_id uuid NOT NULL,
            version integer NOT NULL,
            status varchar(10) NOT NULL,
            inputs_digest varchar(64) NOT NULL,
            seed bigint NOT NULL,
            engine_version varchar(16) NOT NULL,
            horizon_days smallint NOT NULL,
            solver_status varchar(12) NOT NULL,
            conflict jsonb,
            summary jsonb NOT NULL DEFAULT '{{}}'::jsonb,
            mandate_versions jsonb NOT NULL DEFAULT '[]'::jsonb,
            triggered_by varchar(10) NOT NULL,
            duration_ms integer,
            created_at {TS},
            superseded_at timestamptz,
            CONSTRAINT uq_helm_plans_book_version UNIQUE (book_id, version),
            {book_fk.format(table="helm_plans")},
            CONSTRAINT ck_helm_plans_status CHECK (status IN ('CURRENT', 'SUPERSEDED')),
            CONSTRAINT ck_helm_plans_solver_status CHECK
                (solver_status IN ('OPTIMAL', 'APPROXIMATE', 'CONFLICT', 'EMPTY')),
            CONSTRAINT ck_helm_plans_trigger CHECK (triggered_by IN ('LEDGER', 'SCHEDULE', 'PERSON', 'EXECUTION')),
            CONSTRAINT ck_helm_plans_digest CHECK (inputs_digest ~ '^[0-9a-f]{{64}}$')
        )
        """
    )
    op.execute("CREATE UNIQUE INDEX uq_helm_plans_current ON helm_plans (book_id) WHERE status = 'CURRENT'")

    op.execute(
        f"""
        CREATE TABLE helm_actions (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            {org},
            book_id uuid NOT NULL,
            plan_id uuid REFERENCES helm_plans (id) ON DELETE SET NULL,
            workspace_id uuid,
            work_item_id uuid,
            promise_id uuid,
            mandate_id uuid REFERENCES helm_mandates (id) ON DELETE SET NULL,
            mandate_version integer,
            kind varchar(20) NOT NULL,
            idempotency_key varchar(200) NOT NULL,
            state varchar(10) NOT NULL,
            autonomy varchar(8) NOT NULL,
            needs_approval boolean NOT NULL DEFAULT true,
            guard_passed boolean NOT NULL DEFAULT false,
            guard jsonb NOT NULL DEFAULT '{{}}'::jsonb,
            amount_micros bigint,
            currency varchar(3),
            run_date date,
            counterparty_key varchar(255),
            title text NOT NULL,
            explanation text NOT NULL,
            value_micros bigint,
            params jsonb NOT NULL DEFAULT '{{}}'::jsonb,
            result jsonb NOT NULL DEFAULT '{{}}'::jsonb,
            undoable boolean NOT NULL DEFAULT false,
            approvals jsonb NOT NULL DEFAULT '[]'::jsonb,
            required_approvals smallint NOT NULL DEFAULT 1,
            decided_by_user_id {user},
            decided_at timestamptz,
            rejected_reason text,
            executed_at timestamptz,
            executed_by_user_id {user},
            certificate_id uuid,
            created_at {TS},
            updated_at {TS},
            CONSTRAINT uq_helm_actions_idempotency_key UNIQUE (idempotency_key),
            {book_fk.format(table="helm_actions")},
            {work_item_fk.format(table="helm_actions")},
            CONSTRAINT ck_helm_actions_kind CHECK (kind IN ({_in((
                "hold_payment", "schedule_payment", "approve_payment", "mark_paid", "draft_dispute",
                "send_reminder", "request_document", "create_task", "decide_renewal", "notify", "webhook"))})),
            CONSTRAINT ck_helm_actions_state CHECK (state IN ('PROPOSED', 'APPROVED', 'EXECUTING', 'EXECUTED',
                'REJECTED', 'BLOCKED', 'UNDONE', 'SUPERSEDED', 'FAILED')),
            CONSTRAINT ck_helm_actions_autonomy CHECK (autonomy IN ('watch', 'propose', 'act')),
            CONSTRAINT ck_helm_actions_currency CHECK (currency IS NULL OR currency ~ '^[A-Z]{{3}}$'),
            CONSTRAINT ck_helm_actions_required_approvals CHECK (required_approvals BETWEEN 1 AND 3),
            CONSTRAINT ck_helm_actions_rejected_reason CHECK (state <> 'REJECTED' OR rejected_reason IS NOT NULL)
        )
        """
    )
    op.execute("CREATE INDEX ix_helm_actions_book_state ON helm_actions (book_id, state)")
    op.execute("CREATE INDEX ix_helm_actions_plan ON helm_actions (plan_id)")
    op.execute("CREATE INDEX ix_helm_actions_work_item ON helm_actions (work_item_id)")

    op.execute(
        f"""
        CREATE TABLE helm_certificates (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            {org},
            book_id uuid NOT NULL,
            action_id uuid REFERENCES helm_actions (id) ON DELETE CASCADE,
            plan_id uuid REFERENCES helm_plans (id) ON DELETE SET NULL,
            stage varchar(10) NOT NULL,
            passed boolean NOT NULL,
            canonical text NOT NULL,
            sha256 varchar(64) NOT NULL,
            created_at {TS},
            {book_fk.format(table="helm_certificates")},
            CONSTRAINT ck_helm_certificates_stage CHECK (stage IN ('PLAN', 'EXECUTION')),
            CONSTRAINT ck_helm_certificates_sha256 CHECK (sha256 ~ '^[0-9a-f]{{64}}$')
        )
        """
    )
    op.execute("CREATE INDEX ix_helm_certificates_action ON helm_certificates (action_id)")
    op.execute("CREATE INDEX ix_helm_certificates_book_created ON helm_certificates (book_id, created_at)")
    # A plan being superseded may set plan_id NULL through the foreign key (ON DELETE SET NULL is
    # an UPDATE); only that one change is let through.
    op.execute(
        """
        CREATE FUNCTION helm_certificates_refuse_update() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF NEW.plan_id IS NULL AND OLD.plan_id IS NOT NULL
               AND NEW.canonical = OLD.canonical AND NEW.sha256 = OLD.sha256 AND NEW.passed = OLD.passed
               AND NEW.stage = OLD.stage AND NEW.action_id IS NOT DISTINCT FROM OLD.action_id THEN
                RETURN NEW;
            END IF;
            RAISE EXCEPTION 'Helm certificates are append-only'
                USING ERRCODE = 'check_violation';
        END;
        $$
        """
    )
    op.execute(
        "CREATE TRIGGER trg_helm_certificates_append_only BEFORE UPDATE ON helm_certificates "
        "FOR EACH ROW EXECUTE FUNCTION helm_certificates_refuse_update()"
    )

    op.execute(
        f"""
        CREATE TABLE helm_backtests (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            {org},
            book_id uuid NOT NULL,
            status varchar(10) NOT NULL,
            months smallint NOT NULL,
            period_start date NOT NULL,
            period_end date NOT NULL,
            preview boolean NOT NULL DEFAULT false,
            mandates jsonb NOT NULL DEFAULT '[]'::jsonb,
            result jsonb NOT NULL DEFAULT '{{}}'::jsonb,
            summary text,
            error text,
            requested_by_user_id {user},
            job_id uuid,
            created_at {TS},
            started_at timestamptz,
            finished_at timestamptz,
            {book_fk.format(table="helm_backtests")},
            CONSTRAINT ck_helm_backtests_status CHECK (status IN ('QUEUED', 'RUNNING', 'DONE', 'FAILED')),
            CONSTRAINT ck_helm_backtests_months CHECK (months BETWEEN 1 AND 24),
            CONSTRAINT ck_helm_backtests_period CHECK (period_start <= period_end)
        )
        """
    )
    op.execute("CREATE INDEX ix_helm_backtests_book_created ON helm_backtests (book_id, created_at)")
    op.execute(
        "CREATE INDEX ix_helm_backtests_organization_preview ON helm_backtests (organization_id, preview, created_at)"
    )

    op.execute(
        f"""
        CREATE TABLE helm_value_events (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            {org},
            book_id uuid NOT NULL,
            workspace_id uuid,
            work_item_id uuid,
            category varchar(24) NOT NULL,
            dedupe_key varchar(64) NOT NULL,
            currency varchar(3) NOT NULL,
            amount_micros bigint NOT NULL,
            occurred_on date NOT NULL,
            month date NOT NULL,
            evidence jsonb NOT NULL DEFAULT '{{}}'::jsonb,
            action_id uuid,
            created_at {TS},
            CONSTRAINT uq_helm_value_events_dedupe_key UNIQUE (dedupe_key),
            {book_fk.format(table="helm_value_events")},
            {work_item_fk.format(table="helm_value_events")},
            CONSTRAINT ck_helm_value_events_category CHECK (category IN
                ('DUPLICATE_BLOCKED', 'OVERCHARGE_RECOVERED', 'DISCOUNT_CAPTURED', 'LATE_FEE_AVOIDED')),
            CONSTRAINT ck_helm_value_events_positive CHECK (amount_micros > 0),
            CONSTRAINT ck_helm_value_events_currency CHECK (currency ~ '^[A-Z]{{3}}$')
        )
        """
    )
    op.execute("CREATE INDEX ix_helm_value_events_book_month ON helm_value_events (book_id, month)")

    op.execute(
        f"""
        CREATE TABLE helm_tasks (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            {org},
            workspace_id uuid NOT NULL,
            book_id uuid NOT NULL,
            action_id uuid REFERENCES helm_actions (id) ON DELETE SET NULL,
            kind varchar(24) NOT NULL,
            work_item_id uuid,
            obligation_id uuid,
            title text NOT NULL,
            body text NOT NULL,
            severity varchar(8) NOT NULL,
            status varchar(10) NOT NULL DEFAULT 'OPEN',
            decision varchar(16),
            decision_note text,
            resolved_by_user_id {user},
            resolved_at timestamptz,
            created_at {TS},
            updated_at {TS},
            {book_fk.format(table="helm_tasks")},
            {workspace_fk.format(table="helm_tasks")},
            {work_item_fk.format(table="helm_tasks")},
            CONSTRAINT ck_helm_tasks_kind CHECK (kind IN ('VERIFY_BANK_ACCOUNT', 'REVIEW_MISSING_PO',
                'DECIDE_RENEWAL', 'REVIEW_BACKLOG', 'REVIEW_DISPUTE', 'GENERAL')),
            CONSTRAINT ck_helm_tasks_status CHECK (status IN ('OPEN', 'DONE', 'CANCELLED')),
            CONSTRAINT ck_helm_tasks_severity CHECK (severity IN ('LOW', 'MEDIUM', 'HIGH')),
            CONSTRAINT ck_helm_tasks_resolved_recorded CHECK (status = 'OPEN' OR resolved_at IS NOT NULL)
        )
        """
    )
    op.execute("CREATE INDEX ix_helm_tasks_workspace_status ON helm_tasks (workspace_id, status, created_at)")
    op.execute("CREATE INDEX ix_helm_tasks_action ON helm_tasks (action_id)")

    # The review hub lists Helm's tasks (kind HELM) beside every other engine's items.
    op.execute("DROP VIEW IF EXISTS review_queue_items")
    op.execute(review_queue_view_v11())
    _review_kind_check(REVIEW_KINDS_AFTER)


def downgrade() -> None:
    op.execute("DELETE FROM review_assignments WHERE kind = 'HELM'")
    _review_kind_check(REVIEW_KINDS_BEFORE)
    op.execute("DROP VIEW IF EXISTS review_queue_items")
    op.execute(_v10())
    for table in TABLES_IN_DROP_ORDER:
        op.execute(f"DROP TABLE IF EXISTS {table}")
    op.execute("DROP FUNCTION IF EXISTS helm_certificates_refuse_update()")
    op.execute("DROP FUNCTION IF EXISTS helm_refuse_update()")
    op.execute("ALTER TABLE workspaces DROP COLUMN IF EXISTS is_sample")

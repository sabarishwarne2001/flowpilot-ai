"""ARCH-50 Step 1 — EXPAND: the Sovereign Edition, RevOps and DR evidence.

Revision ID: arch50_step1_sovereign_revops
Revises: arch49_step1_process_intelligence

ARCH50-S1:migration. Expand only. Nothing existing is dropped or narrowed; no automation event is added (the
outbox vocabulary is untouched). ONE existing function is WIDENED, never narrowed: the pipeline stage guard
(see STAGE GUARD below; the downgrade restores the ARCH-10 body exactly).

STAGE GUARD (ARCH50-S1:stage-guard -- found by GA-2)
====================================================
`work_items_stage_transition_guard()` (ARCH-10 step 7) still carries the fourteen transitions of ARCH-10, while
`app/services/pipeline_state.STAGE_TRANSITIONS` -- the table the application checks every transition against --
has declared seven more legal since 2026-09-06: re-queueing from EXTRACTING, EXTRACTED and ENRICHING, and retrying
from FAILED or QUOTA_BLOCKED straight into EXTRACTING or ENRICHING. PostgreSQL refused all seven (23514), so a
quota-blocked document whose extraction was run again after the limit was raised, or a failed document whose
enrichment was retried, failed in the database with an IntegrityError instead of proceeding.
`scripts/verify_arch10_step9.py` G2.2 ("the trigger's table matches pipeline_state") had failed ever since; GA-2
was its first run on a clean database. The guard now carries exactly the application's table (verify_arch50 T2
proves the parity; D3 drives the real function: the seven now pass, an illegal move is still refused).

CHAIN POSITION
==============
Inserted between arch49_step1_process_intelligence (the ARCH-49 release head) and
arch40_step3_contract_ai_settings (the flag-gated, lossy contract step), as hm1 and ARCH-41 to 49 were: the
contract step now revises this revision, so there is still ONE file head. run_arch50.ps1 upgrades to this
revision by name and handles a database whose contract already ran.

FOURTEEN TABLES
===============
Sovereign edition
  egress_policies          an organization's lockdown switch (no row = no lockdown)
  egress_allow_rules       its allow rules: a channel it governs (or any), a host / *.suffix / IP / CIDR, a port
  egress_refusals          refused connections, one row per (organization, channel, host, port, reason, hour)
                           with a count -- an expression UNIQUE lets a NULL organization (a deployment-level
                           refusal) bucket too
  platform_licences        uploaded licences exactly as signed; at most one current
DR evidence
  dr_heartbeats            a row a minute on the primary: after a restore, the newest one present bounds the loss
  dr_drills                every measured drill: RPO, RTO, the target and what was actually recovered
RevOps
  plan_price_books         PLAN prices per currency (USD, INR), DRAFT -> PUBLISHED (immutable, digested) -> RETIRED;
                           one published book per currency
  plan_price_book_entries  (tier, interval) -> amount and gateway price; a trigger keeps one gateway price on one
                           (tier, interval, currency), another refuses any change to a published book's entries
  promo_codes              percent OR amount off, once / repeating / forever, redemption cap, deadline, scope
  promo_redemptions        RESERVED at checkout -> REDEEMED when the subscription or contract starts; one live
                           redemption per (code, organization)
  checkout_selections      what each checkout sold (tier, interval, currency, amount, seats)
  enterprise_contracts     invoiced contracts: term, seats, amount per period, tax, payment terms, PO; one ACTIVE
                           contract per organization
  contract_invoices        one per (contract, period) unless voided; total = subtotal - discount + tax
  revenue_snapshots        a closed month per currency: MRR, ARR, new / expansion / contraction / churn
"""

from __future__ import annotations

from alembic import op

revision = "arch50_step1_sovereign_revops"
down_revision = "arch49_step1_process_intelligence"
branch_labels = None
depends_on = None

# Vocabulary (the services import their own copies; verify_arch50 T2 proves the two agree).
CHANNELS = ("WEBHOOK", "WAREHOUSE", "ERP_HTTP", "ERP_SFTP", "IDENTITY", "SMTP_TENANT", "LLM_PROVIDER",
            "SMTP_PLATFORM", "LLM_LOCAL", "STORAGE", "BILLING", "INTERNAL", "DNS", "BACKUP")
TENANT_CHANNELS = ("WEBHOOK", "WAREHOUSE", "ERP_HTTP", "ERP_SFTP", "IDENTITY", "SMTP_TENANT", "LLM_PROVIDER")
REASONS = ("DEPLOYMENT_DENY", "TENANT_LOCKDOWN", "OPERATOR_ONLY", "INVALID_DESTINATION", "POLICY_UNAVAILABLE")
MODES = ("open", "deny")
DRILL_KINDS = ("PITR", "RESTORE", "CHAOS")
DRILL_OUTCOMES = ("PASSED", "FAILED")
CURRENCIES = ("USD", "INR")
PLAN_INTERVALS = ("month", "year")
CONTRACT_INTERVALS = ("month", "quarter", "year")
BOOK_STATUSES = ("DRAFT", "PUBLISHED", "RETIRED")
PROMO_DURATIONS = ("ONCE", "REPEATING", "FOREVER")
REDEMPTION_STATUSES = ("RESERVED", "REDEEMED", "RELEASED", "EXPIRED")
CONTRACT_STATUSES = ("DRAFT", "ACTIVE", "ENDED", "CANCELLED")
CONTRACT_END_REASONS = ("TERM_ENDED", "CANCELLED", "SUPERSEDED", "NON_PAYMENT")
INVOICE_STATUSES = ("ISSUED", "PAID", "VOID")
GATEWAYS = ("STRIPE", "DODO")
MAX_RULES_PER_ORGANIZATION = 200

TABLES_IN_DROP_ORDER = ("revenue_snapshots", "contract_invoices", "checkout_selections", "promo_redemptions",
                        "enterprise_contracts", "promo_codes", "plan_price_book_entries", "plan_price_books",
                        "dr_drills", "dr_heartbeats", "platform_licences", "egress_refusals", "egress_allow_rules",
                        "egress_policies")
FUNCTIONS = ("plan_price_books_guard()", "plan_price_book_entries_guard()",
             "plan_price_book_entries_gateway_single_plan()")


#: The ARCH-10 guard's table (arch10_step7_pipeline_expand.LEGAL_TRANSITIONS), restored by the downgrade.
STAGE_TRANSITIONS_ARCH10 = (
    ("QUEUED", "EXTRACTING"), ("QUEUED", "FAILED"), ("QUEUED", "QUOTA_BLOCKED"),
    ("EXTRACTING", "EXTRACTED"), ("EXTRACTING", "FAILED"), ("EXTRACTING", "QUOTA_BLOCKED"),
    ("EXTRACTED", "ENRICHING"), ("EXTRACTED", "COMPLETED"), ("EXTRACTED", "FAILED"),
    ("ENRICHING", "COMPLETED"), ("ENRICHING", "FAILED"),
    ("COMPLETED", "QUEUED"), ("FAILED", "QUEUED"), ("QUOTA_BLOCKED", "QUEUED"),
)
#: The seven the application declares legal and the ARCH-10 guard refused (app/services/pipeline_state.py).
STAGE_TRANSITIONS_ADDED = (
    ("EXTRACTING", "QUEUED"), ("EXTRACTED", "QUEUED"), ("ENRICHING", "QUEUED"),
    ("FAILED", "EXTRACTING"), ("FAILED", "ENRICHING"),
    ("QUOTA_BLOCKED", "EXTRACTING"), ("QUOTA_BLOCKED", "ENRICHING"),
)
STAGE_TRANSITIONS = STAGE_TRANSITIONS_ARCH10 + STAGE_TRANSITIONS_ADDED


def stage_guard_sql(pairs: tuple[tuple[str, str], ...]) -> str:
    """The ARCH-10 function, same body, with the given transition table."""
    values = ", ".join(f"('{source}','{target}')" for source, target in pairs)
    return f"""
CREATE OR REPLACE FUNCTION work_items_stage_transition_guard()
RETURNS TRIGGER AS $$
DECLARE
    legal BOOLEAN;
BEGIN
    IF NEW.pipeline_stage IS NOT DISTINCT FROM OLD.pipeline_stage THEN
        RETURN NEW;
    END IF;

    IF OLD.pipeline_stage IS NULL THEN
        RETURN NEW;
    END IF;

    SELECT EXISTS (
        SELECT 1
        FROM (VALUES {values}) AS t(src, dst)
        WHERE t.src = OLD.pipeline_stage::text
          AND t.dst = NEW.pipeline_stage::text
    ) INTO legal;

    IF NOT legal THEN
        RAISE EXCEPTION
            'work_items %: illegal pipeline transition % -> %',
            OLD.id, OLD.pipeline_stage, NEW.pipeline_stage
            USING ERRCODE = '23514';
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""


def _in(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{v}'" for v in values)


def upgrade() -> None:
    # ------------------------------------------------------------------ sovereign
    op.execute(
        """
        CREATE TABLE egress_policies (
            organization_id uuid PRIMARY KEY REFERENCES organizations(id) ON DELETE CASCADE,
            lockdown_enabled boolean NOT NULL DEFAULT false,
            updated_by_user_id uuid REFERENCES users(id) ON DELETE SET NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT ck_egress_policies_updated_after_created CHECK (updated_at >= created_at)
        )
        """
    )
    op.execute(
        f"""
        CREATE TABLE egress_allow_rules (
            id uuid PRIMARY KEY,
            organization_id uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
            channel varchar(16),
            host_pattern varchar(253) NOT NULL,
            port integer,
            note varchar(200),
            created_by_user_id uuid REFERENCES users(id) ON DELETE SET NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT ck_egress_allow_rules_channel_governed CHECK (channel IS NULL OR channel IN ({_in(TENANT_CHANNELS)})),
            CONSTRAINT ck_egress_allow_rules_port_range CHECK (port IS NULL OR (port >= 1 AND port <= 65535)),
            CONSTRAINT ck_egress_allow_rules_pattern_shape CHECK (
                length(host_pattern) BETWEEN 1 AND 253
                AND host_pattern = lower(host_pattern)
                AND host_pattern !~ '[[:space:]?#@]'
                AND strpos(host_pattern, chr(92)) = 0
                AND strpos(host_pattern, '://') = 0),
            CONSTRAINT ck_egress_allow_rules_no_everything CHECK (host_pattern NOT IN ('*', '*.', '0.0.0.0/0', '::/0'))
        )
        """
    )
    op.execute("CREATE UNIQUE INDEX uq_egress_allow_rules_scope ON egress_allow_rules "
               "(organization_id, (COALESCE(channel, '')), host_pattern, (COALESCE(port, 0)))")
    op.execute(
        f"""
        CREATE TABLE egress_refusals (
            id uuid PRIMARY KEY,
            organization_id uuid REFERENCES organizations(id) ON DELETE CASCADE,
            channel varchar(16) NOT NULL,
            host varchar(253) NOT NULL,
            port integer NOT NULL DEFAULT 0,
            reason varchar(24) NOT NULL,
            mode varchar(8) NOT NULL,
            bucket_start timestamptz NOT NULL,
            first_at timestamptz NOT NULL,
            last_at timestamptz NOT NULL,
            count integer NOT NULL DEFAULT 1,
            CONSTRAINT ck_egress_refusals_channel_known CHECK (channel IN ({_in(CHANNELS)})),
            CONSTRAINT ck_egress_refusals_reason_known CHECK (reason IN ({_in(REASONS)})),
            CONSTRAINT ck_egress_refusals_mode_known CHECK (mode IN ({_in(MODES)})),
            CONSTRAINT ck_egress_refusals_count_positive CHECK (count >= 1),
            CONSTRAINT ck_egress_refusals_window CHECK (last_at >= first_at AND first_at >= bucket_start),
            CONSTRAINT ck_egress_refusals_port_range CHECK (port >= 0 AND port <= 65535),
            CONSTRAINT ck_egress_refusals_tenant_reason_has_tenant CHECK (
                reason <> 'TENANT_LOCKDOWN' OR organization_id IS NOT NULL)
        )
        """
    )
    op.execute("CREATE UNIQUE INDEX uq_egress_refusals_bucket ON egress_refusals "
               "((COALESCE(organization_id, '00000000-0000-0000-0000-000000000000'::uuid)), channel, host, port, "
               "reason, bucket_start)")
    op.execute("CREATE INDEX ix_egress_refusals_org_time ON egress_refusals (organization_id, last_at DESC)")
    op.execute("CREATE INDEX ix_egress_refusals_time ON egress_refusals (last_at DESC)")
    op.execute(
        """
        CREATE TABLE platform_licences (
            id uuid PRIMARY KEY,
            licence_id varchar(64) NOT NULL,
            key_id varchar(40) NOT NULL,
            payload jsonb NOT NULL,
            signature varchar(100) NOT NULL,
            is_current boolean NOT NULL DEFAULT false,
            uploaded_by_user_id uuid REFERENCES users(id) ON DELETE SET NULL,
            uploaded_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT ck_platform_licences_key_id_shape CHECK (key_id ~ '^ed25519:[0-9a-f]{16}$'),
            CONSTRAINT ck_platform_licences_payload_object CHECK (jsonb_typeof(payload) = 'object'),
            CONSTRAINT ck_platform_licences_id_matches_payload CHECK (payload ->> 'licence_id' = licence_id)
        )
        """
    )
    op.execute("CREATE UNIQUE INDEX uq_platform_licences_current ON platform_licences (is_current) WHERE is_current")
    op.execute(
        """
        CREATE TABLE dr_heartbeats (
            id bigserial PRIMARY KEY,
            beat_at timestamptz NOT NULL DEFAULT now(),
            node varchar(64) NOT NULL,
            CONSTRAINT ck_dr_heartbeats_node_not_blank CHECK (length(node) > 0)
        )
        """
    )
    op.execute("CREATE INDEX ix_dr_heartbeats_beat_at ON dr_heartbeats (beat_at DESC)")
    op.execute(
        f"""
        CREATE TABLE dr_drills (
            id uuid PRIMARY KEY,
            kind varchar(16) NOT NULL,
            outcome varchar(8) NOT NULL,
            started_at timestamptz NOT NULL,
            finished_at timestamptz NOT NULL,
            target_time timestamptz,
            recovered_to timestamptz,
            rpo_seconds numeric(12, 3),
            rto_seconds numeric(12, 3),
            host varchar(128) NOT NULL DEFAULT '',
            details jsonb NOT NULL DEFAULT '{{}}'::jsonb,
            CONSTRAINT ck_dr_drills_kind_known CHECK (kind IN ({_in(DRILL_KINDS)})),
            CONSTRAINT ck_dr_drills_outcome_known CHECK (outcome IN ({_in(DRILL_OUTCOMES)})),
            CONSTRAINT ck_dr_drills_ordered CHECK (finished_at >= started_at),
            CONSTRAINT ck_dr_drills_measures_non_negative CHECK (
                (rpo_seconds IS NULL OR rpo_seconds >= 0) AND (rto_seconds IS NULL OR rto_seconds >= 0)),
            CONSTRAINT ck_dr_drills_passed_pitr_is_measured CHECK (
                NOT (kind = 'PITR' AND outcome = 'PASSED')
                OR (rpo_seconds IS NOT NULL AND rto_seconds IS NOT NULL AND recovered_to IS NOT NULL))
        )
        """
    )
    op.execute("CREATE INDEX ix_dr_drills_kind_time ON dr_drills (kind, finished_at DESC)")

    # ------------------------------------------------------------------ RevOps
    op.execute(
        f"""
        CREATE TABLE plan_price_books (
            id uuid PRIMARY KEY,
            code varchar(32) NOT NULL UNIQUE,
            currency varchar(3) NOT NULL,
            status varchar(12) NOT NULL DEFAULT 'DRAFT',
            notes varchar(500),
            content_digest varchar(64),
            published_at timestamptz,
            published_by_user_id uuid REFERENCES users(id) ON DELETE SET NULL,
            retired_at timestamptz,
            created_by_user_id uuid REFERENCES users(id) ON DELETE SET NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT ck_plan_price_books_code_shape CHECK (code ~ '^[A-Z0-9][A-Z0-9_-]{{1,31}}$'),
            CONSTRAINT ck_plan_price_books_currency_known CHECK (currency IN ({_in(CURRENCIES)})),
            CONSTRAINT ck_plan_price_books_status_known CHECK (status IN ({_in(BOOK_STATUSES)})),
            CONSTRAINT ck_plan_price_books_published_is_digested CHECK (
                (status = 'DRAFT') = (published_at IS NULL) AND (published_at IS NULL) = (content_digest IS NULL)),
            CONSTRAINT ck_plan_price_books_retired_has_time CHECK ((status = 'RETIRED') = (retired_at IS NOT NULL))
        )
        """
    )
    op.execute("CREATE UNIQUE INDEX uq_plan_price_books_one_published ON plan_price_books (currency) "
               "WHERE status = 'PUBLISHED'")
    op.execute(
        f"""
        CREATE TABLE plan_price_book_entries (
            id uuid PRIMARY KEY,
            book_id uuid NOT NULL REFERENCES plan_price_books(id) ON DELETE CASCADE,
            tier_key varchar(32) NOT NULL,
            billing_interval varchar(8) NOT NULL,
            unit_amount_micros bigint NOT NULL,
            gateway_price_id varchar(255),
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT ck_plan_price_book_entries_interval_known CHECK (billing_interval IN ({_in(PLAN_INTERVALS)})),
            CONSTRAINT ck_plan_price_book_entries_amount_non_negative CHECK (unit_amount_micros >= 0),
            CONSTRAINT ck_plan_price_book_entries_tier_not_blank CHECK (length(tier_key) > 0),
            CONSTRAINT ck_plan_price_book_entries_minor_units CHECK (unit_amount_micros % 10000 = 0),
            CONSTRAINT uq_plan_price_book_entries_scope UNIQUE (book_id, tier_key, billing_interval)
        )
        """
    )
    op.execute(
        """
        CREATE FUNCTION plan_price_books_guard() RETURNS trigger AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                IF OLD.status <> 'DRAFT' THEN
                    RAISE EXCEPTION 'plan price book % is %; only a draft can be deleted', OLD.code, OLD.status;
                END IF;
                RETURN OLD;
            END IF;
            IF OLD.status <> 'DRAFT' THEN
                IF NEW.code IS DISTINCT FROM OLD.code OR NEW.currency IS DISTINCT FROM OLD.currency
                   OR NEW.content_digest IS DISTINCT FROM OLD.content_digest
                   OR NEW.published_at IS DISTINCT FROM OLD.published_at THEN
                    RAISE EXCEPTION 'plan price book % is published and immutable', OLD.code;
                END IF;
                IF OLD.status = 'RETIRED' AND NEW.status <> 'RETIRED' THEN
                    RAISE EXCEPTION 'plan price book % is retired', OLD.code;
                END IF;
                IF OLD.status = 'PUBLISHED' AND NEW.status = 'DRAFT' THEN
                    RAISE EXCEPTION 'plan price book % cannot return to draft', OLD.code;
                END IF;
            END IF;
            RETURN NEW;
        END
        $$ LANGUAGE plpgsql
        """
    )
    op.execute("CREATE TRIGGER trg_plan_price_books_guard BEFORE UPDATE OR DELETE ON plan_price_books "
               "FOR EACH ROW EXECUTE FUNCTION plan_price_books_guard()")
    op.execute(
        """
        CREATE FUNCTION plan_price_book_entries_guard() RETURNS trigger AS $$
        DECLARE
            book_status text;
        BEGIN
            SELECT status INTO book_status FROM plan_price_books
             WHERE id = COALESCE(NEW.book_id, OLD.book_id);
            IF book_status IS NOT NULL AND book_status <> 'DRAFT' THEN
                RAISE EXCEPTION 'the entries of a % plan price book are immutable', book_status;
            END IF;
            IF TG_OP = 'UPDATE' AND NEW.book_id IS DISTINCT FROM OLD.book_id THEN
                RAISE EXCEPTION 'an entry cannot move between plan price books';
            END IF;
            IF TG_OP = 'DELETE' THEN
                RETURN OLD;
            END IF;
            RETURN NEW;
        END
        $$ LANGUAGE plpgsql
        """
    )
    op.execute("CREATE TRIGGER trg_plan_price_book_entries_guard BEFORE INSERT OR UPDATE OR DELETE ON "
               "plan_price_book_entries FOR EACH ROW EXECUTE FUNCTION plan_price_book_entries_guard()")
    op.execute(
        """
        CREATE FUNCTION plan_price_book_entries_gateway_single_plan() RETURNS trigger AS $$
        DECLARE
            my_currency text;
        BEGIN
            IF NEW.gateway_price_id IS NULL THEN
                RETURN NEW;
            END IF;
            PERFORM pg_advisory_xact_lock(hashtext('plan_price_book_entries.gateway_price_id'));
            SELECT currency INTO my_currency FROM plan_price_books WHERE id = NEW.book_id;
            IF EXISTS (
                SELECT 1 FROM plan_price_book_entries e JOIN plan_price_books b ON b.id = e.book_id
                 WHERE e.gateway_price_id = NEW.gateway_price_id AND e.id <> NEW.id
                   AND (e.tier_key <> NEW.tier_key OR e.billing_interval <> NEW.billing_interval
                        OR b.currency <> my_currency)
            ) OR EXISTS (
                SELECT 1 FROM quota_tiers q WHERE q.gateway_price_id = NEW.gateway_price_id AND q.key <> NEW.tier_key
            ) THEN
                RAISE EXCEPTION 'gateway price % already identifies another plan, interval or currency',
                    NEW.gateway_price_id;
            END IF;
            RETURN NEW;
        END
        $$ LANGUAGE plpgsql
        """
    )
    op.execute("CREATE TRIGGER trg_plan_price_book_entries_gateway_single_plan BEFORE INSERT OR UPDATE ON "
               "plan_price_book_entries FOR EACH ROW EXECUTE FUNCTION plan_price_book_entries_gateway_single_plan()")
    op.execute(
        f"""
        CREATE TABLE promo_codes (
            id uuid PRIMARY KEY,
            code varchar(32) NOT NULL UNIQUE,
            description varchar(200),
            percent_off numeric(5, 2),
            amount_off_micros bigint,
            currency varchar(3),
            duration varchar(10) NOT NULL,
            duration_in_months integer,
            max_redemptions integer,
            times_redeemed integer NOT NULL DEFAULT 0,
            redeem_by timestamptz,
            applies_to_tiers varchar(32)[],
            applies_to_intervals varchar(8)[],
            first_subscription_only boolean NOT NULL DEFAULT false,
            gateway_coupon_id varchar(255),
            is_active boolean NOT NULL DEFAULT true,
            created_by_user_id uuid REFERENCES users(id) ON DELETE SET NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT ck_promo_codes_code_shape CHECK (code ~ '^[A-Z0-9][A-Z0-9_-]{{2,31}}$'),
            CONSTRAINT ck_promo_codes_one_discount CHECK ((percent_off IS NULL) <> (amount_off_micros IS NULL)),
            CONSTRAINT ck_promo_codes_percent_range CHECK (percent_off IS NULL OR (percent_off > 0 AND percent_off <= 100)),
            CONSTRAINT ck_promo_codes_amount_has_currency CHECK (
                amount_off_micros IS NULL OR (amount_off_micros > 0 AND currency IS NOT NULL)),
            CONSTRAINT ck_promo_codes_currency_known CHECK (currency IS NULL OR currency IN ({_in(CURRENCIES)})),
            CONSTRAINT ck_promo_codes_duration_known CHECK (duration IN ({_in(PROMO_DURATIONS)})),
            CONSTRAINT ck_promo_codes_months_iff_repeating CHECK (
                (duration = 'REPEATING') = (duration_in_months IS NOT NULL)
                AND (duration_in_months IS NULL OR duration_in_months BETWEEN 1 AND 36)),
            CONSTRAINT ck_promo_codes_cap_positive CHECK (max_redemptions IS NULL OR max_redemptions >= 1),
            CONSTRAINT ck_promo_codes_within_cap CHECK (
                times_redeemed >= 0 AND (max_redemptions IS NULL OR times_redeemed <= max_redemptions)),
            CONSTRAINT ck_promo_codes_intervals_known CHECK (
                applies_to_intervals IS NULL OR applies_to_intervals <@ ARRAY[{_in(PLAN_INTERVALS)}]::varchar[])
        )
        """
    )
    op.execute(
        f"""
        CREATE TABLE enterprise_contracts (
            id uuid PRIMARY KEY,
            organization_id uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
            contract_number varchar(32) NOT NULL UNIQUE,
            tier_key varchar(32) NOT NULL,
            seats integer NOT NULL,
            currency varchar(3) NOT NULL,
            billing_interval varchar(8) NOT NULL,
            amount_per_period_micros bigint NOT NULL,
            tax_rate_bps integer NOT NULL DEFAULT 0,
            term_start date NOT NULL,
            term_end date NOT NULL,
            payment_terms_days integer NOT NULL DEFAULT 30,
            po_number varchar(64),
            billing_email varchar(254),
            status varchar(10) NOT NULL DEFAULT 'DRAFT',
            promo_code_id uuid REFERENCES promo_codes(id) ON DELETE RESTRICT,
            activated_at timestamptz,
            activated_by_user_id uuid REFERENCES users(id) ON DELETE SET NULL,
            ended_at timestamptz,
            end_reason varchar(16),
            notes varchar(1000),
            created_by_user_id uuid REFERENCES users(id) ON DELETE SET NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT ck_enterprise_contracts_number_shape CHECK (contract_number ~ '^[A-Z0-9][A-Z0-9/_-]{{2,31}}$'),
            CONSTRAINT ck_enterprise_contracts_seats_positive CHECK (seats >= 1),
            CONSTRAINT ck_enterprise_contracts_currency_known CHECK (currency IN ({_in(CURRENCIES)})),
            CONSTRAINT ck_enterprise_contracts_interval_known CHECK (billing_interval IN ({_in(CONTRACT_INTERVALS)})),
            CONSTRAINT ck_enterprise_contracts_amount_non_negative CHECK (amount_per_period_micros >= 0),
            CONSTRAINT ck_enterprise_contracts_minor_units CHECK (amount_per_period_micros % 10000 = 0),
            CONSTRAINT ck_enterprise_contracts_tax_range CHECK (tax_rate_bps BETWEEN 0 AND 10000),
            CONSTRAINT ck_enterprise_contracts_term_ordered CHECK (term_end > term_start),
            CONSTRAINT ck_enterprise_contracts_terms_range CHECK (payment_terms_days BETWEEN 0 AND 120),
            CONSTRAINT ck_enterprise_contracts_status_known CHECK (status IN ({_in(CONTRACT_STATUSES)})),
            CONSTRAINT ck_enterprise_contracts_active_was_activated CHECK (
                status NOT IN ('ACTIVE', 'ENDED') OR activated_at IS NOT NULL),
            CONSTRAINT ck_enterprise_contracts_ended_has_reason CHECK (
                (status IN ('ENDED', 'CANCELLED')) = (ended_at IS NOT NULL AND end_reason IS NOT NULL)),
            CONSTRAINT ck_enterprise_contracts_reason_known CHECK (
                end_reason IS NULL OR end_reason IN ({_in(CONTRACT_END_REASONS)}))
        )
        """
    )
    op.execute("CREATE UNIQUE INDEX uq_enterprise_contracts_one_active ON enterprise_contracts (organization_id) "
               "WHERE status = 'ACTIVE'")
    op.execute(
        f"""
        CREATE TABLE promo_redemptions (
            id uuid PRIMARY KEY,
            promo_code_id uuid NOT NULL REFERENCES promo_codes(id) ON DELETE RESTRICT,
            organization_id uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
            status varchar(10) NOT NULL,
            tier_key varchar(32) NOT NULL,
            billing_interval varchar(8) NOT NULL,
            currency varchar(3) NOT NULL,
            list_amount_micros bigint NOT NULL,
            discount_micros bigint NOT NULL,
            contract_id uuid REFERENCES enterprise_contracts(id) ON DELETE SET NULL,
            reserved_at timestamptz NOT NULL,
            expires_at timestamptz NOT NULL,
            redeemed_at timestamptz,
            released_at timestamptz,
            CONSTRAINT ck_promo_redemptions_status_known CHECK (status IN ({_in(REDEMPTION_STATUSES)})),
            CONSTRAINT ck_promo_redemptions_interval_known CHECK (billing_interval IN ({_in(CONTRACT_INTERVALS)})),
            CONSTRAINT ck_promo_redemptions_currency_known CHECK (currency IN ({_in(CURRENCIES)})),
            CONSTRAINT ck_promo_redemptions_discount_bounded CHECK (
                discount_micros >= 0 AND list_amount_micros >= 0 AND discount_micros <= list_amount_micros),
            CONSTRAINT ck_promo_redemptions_redeemed_has_time CHECK ((status = 'REDEEMED') = (redeemed_at IS NOT NULL)),
            CONSTRAINT ck_promo_redemptions_released_has_time CHECK (
                (status IN ('RELEASED', 'EXPIRED')) = (released_at IS NOT NULL)),
            CONSTRAINT ck_promo_redemptions_expiry_after_reserve CHECK (expires_at > reserved_at)
        )
        """
    )
    op.execute("CREATE UNIQUE INDEX uq_promo_redemptions_one_live ON promo_redemptions (promo_code_id, organization_id) "
               "WHERE status IN ('RESERVED', 'REDEEMED')")
    op.execute("CREATE INDEX ix_promo_redemptions_reserved ON promo_redemptions (expires_at) WHERE status = 'RESERVED'")
    op.execute(
        f"""
        CREATE TABLE checkout_selections (
            id uuid PRIMARY KEY,
            organization_id uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
            tier_key varchar(32) NOT NULL,
            billing_interval varchar(8) NOT NULL,
            currency varchar(3) NOT NULL,
            book_entry_id uuid REFERENCES plan_price_book_entries(id) ON DELETE SET NULL,
            unit_amount_micros bigint NOT NULL,
            seats integer NOT NULL,
            promo_redemption_id uuid REFERENCES promo_redemptions(id) ON DELETE SET NULL,
            gateway varchar(16) NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT ck_checkout_selections_interval_known CHECK (billing_interval IN ({_in(PLAN_INTERVALS)})),
            CONSTRAINT ck_checkout_selections_currency_known CHECK (currency IN ({_in(CURRENCIES)})),
            CONSTRAINT ck_checkout_selections_amount_non_negative CHECK (unit_amount_micros >= 0),
            CONSTRAINT ck_checkout_selections_seats_positive CHECK (seats >= 1),
            CONSTRAINT ck_checkout_selections_gateway_known CHECK (gateway IN ({_in(GATEWAYS)}))
        )
        """
    )
    op.execute("CREATE INDEX ix_checkout_selections_org_time ON checkout_selections (organization_id, created_at DESC)")
    op.execute(
        f"""
        CREATE TABLE contract_invoices (
            id uuid PRIMARY KEY,
            contract_id uuid NOT NULL REFERENCES enterprise_contracts(id) ON DELETE CASCADE,
            organization_id uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
            invoice_number varchar(32) NOT NULL UNIQUE,
            period_start date NOT NULL,
            period_end date NOT NULL,
            currency varchar(3) NOT NULL,
            subtotal_micros bigint NOT NULL,
            discount_micros bigint NOT NULL DEFAULT 0,
            tax_micros bigint NOT NULL DEFAULT 0,
            total_micros bigint NOT NULL,
            status varchar(8) NOT NULL DEFAULT 'ISSUED',
            issued_at timestamptz NOT NULL,
            due_at timestamptz NOT NULL,
            paid_at timestamptz,
            payment_reference varchar(128),
            void_reason varchar(200),
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT ck_contract_invoices_status_known CHECK (status IN ({_in(INVOICE_STATUSES)})),
            CONSTRAINT ck_contract_invoices_currency_known CHECK (currency IN ({_in(CURRENCIES)})),
            CONSTRAINT ck_contract_invoices_period_ordered CHECK (period_end > period_start),
            CONSTRAINT ck_contract_invoices_due_after_issue CHECK (due_at >= issued_at),
            CONSTRAINT ck_contract_invoices_arithmetic CHECK (
                subtotal_micros >= 0 AND discount_micros >= 0 AND tax_micros >= 0
                AND discount_micros <= subtotal_micros
                AND total_micros = subtotal_micros - discount_micros + tax_micros),
            CONSTRAINT ck_contract_invoices_paid_has_time CHECK ((status = 'PAID') = (paid_at IS NOT NULL)),
            CONSTRAINT ck_contract_invoices_void_has_reason CHECK ((status = 'VOID') = (void_reason IS NOT NULL))
        )
        """
    )
    op.execute("CREATE UNIQUE INDEX uq_contract_invoices_period ON contract_invoices (contract_id, period_start) "
               "WHERE status <> 'VOID'")
    op.execute("CREATE INDEX ix_contract_invoices_open ON contract_invoices (due_at) WHERE status = 'ISSUED'")
    op.execute(
        f"""
        CREATE TABLE revenue_snapshots (
            id uuid PRIMARY KEY,
            month date NOT NULL,
            currency varchar(3) NOT NULL,
            mrr_micros bigint NOT NULL,
            arr_micros bigint NOT NULL,
            new_mrr_micros bigint NOT NULL DEFAULT 0,
            expansion_mrr_micros bigint NOT NULL DEFAULT 0,
            contraction_mrr_micros bigint NOT NULL DEFAULT 0,
            churned_mrr_micros bigint NOT NULL DEFAULT 0,
            active_customers integer NOT NULL,
            unpriced_customers integer NOT NULL DEFAULT 0,
            details jsonb NOT NULL DEFAULT '{{}}'::jsonb,
            computed_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT ck_revenue_snapshots_first_of_month CHECK (extract(day FROM month) = 1),
            CONSTRAINT ck_revenue_snapshots_currency_known CHECK (currency IN ({_in(CURRENCIES)})),
            CONSTRAINT ck_revenue_snapshots_arr_is_12_mrr CHECK (arr_micros = 12 * mrr_micros),
            CONSTRAINT ck_revenue_snapshots_non_negative CHECK (
                mrr_micros >= 0 AND new_mrr_micros >= 0 AND expansion_mrr_micros >= 0
                AND contraction_mrr_micros >= 0 AND churned_mrr_micros >= 0
                AND active_customers >= 0 AND unpriced_customers >= 0),
            CONSTRAINT uq_revenue_snapshots_month UNIQUE (month, currency)
        )
        """
    )

    # ------------------------------------------------------------------ the pipeline stage guard (GA-2)
    op.execute(stage_guard_sql(STAGE_TRANSITIONS))


def downgrade() -> None:
    op.execute(stage_guard_sql(STAGE_TRANSITIONS_ARCH10))
    for table in TABLES_IN_DROP_ORDER:
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
    for function in FUNCTIONS:
        op.execute(f"DROP FUNCTION IF EXISTS {function}")

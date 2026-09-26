"""ARCH-47 Step 1 — EXPAND: ERP & System-of-Record Posting.

Revision ID: arch47_step1_erp_posting
Revises: arch46_step1_obligations

ARCH47-S1:migration. Expand only. The whole of ARCH-47 in one revision:
nothing existing is dropped or narrowed.

CHAIN POSITION
==============

Inserted between arch46_step1_obligations (the ARCH-46 release head) and
arch40_step3_contract_ai_settings (the flag-gated, lossy contract step), as hm1
and ARCH-41 to 46 were: the contract step now revises this revision, so there
is still ONE file head and `alembic upgrade head` still refuses the contract
without ARCH40_CONTRACT. run_arch47.ps1 upgrades to this revision by name and
handles a database whose contract already ran.

WHAT IT ADDS
============

  erp_targets           where approved outcomes are posted: a format (CSV,
                        XLSX, X12, UBL, TALLY, JSON), a transport (DOWNLOAD,
                        SFTP, HTTP) and, for JSON, a preset (QuickBooks Online,
                        Zoho Books, Business Central, S/4HANA OData, NetSuite
                        REST, generic REST / OData). CHECKs tie the ack mode and
                        the auth mode to the transport and the preset to JSON.
                        Credentials are one Fernet ciphertext (the configured
                        EMAIL_ENCRYPTION_KEYS) with a 12-hex fingerprint; the
                        plain value is never stored. An X12 control-number
                        sequence per target (interchange numbers must rise).
  erp_mappings          the mapping from the canonical object to the target's
                        fields, VERSIONED per workspace, target and object kind:
                        a new version is a new row, one ACTIVE per (target,
                        object) by a partial UNIQUE index; every posting names
                        the version it was rendered with.
  erp_lookup_tables     named string -> string tables the mapping language can
                        look values up in (vendor codes, GL accounts, tax
                        codes); CHECKed to be a flat object of strings, at most
                        5000 entries.
  erp_postings          THE IDEMPOTENCY LEDGER: UNIQUE (target, object kind,
                        source kind, source id) -- one posting per object and
                        target, whatever retries, sweeps, flows or double
                        clicks happen -- and a UNIQUE sha256 idempotency key
                        sent to targets that honour one. CHECKs tie every state
                        to its timestamps (DONE is acknowledged, SENDING holds a
                        lease, RETRYING has a time). The rendered payload is
                        frozen at planning time. A document's deletion clears
                        only work_item_id (the ledger is a financial record).
  erp_posting_attempts  every step the ledger took (render, send, probe,
                        acknowledgement, review), numbered per posting.
  outbox vocabulary     trigger.posting.failed becomes a legal INTERNAL event
                        (the ARCH-37 CHECK is rebuilt, same shape).
  review hub            a ninth kind, POSTING (reason POSTING_EXCEPTION): the
                        CHECK is recreated and the view is ARCH-46's text plus
                        one arm (loaded, not copied).
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

from alembic import op

revision = "arch47_step1_erp_posting"
down_revision = "arch46_step1_obligations"
branch_labels = None
depends_on = None

#: ARCH47-S1:vocabulary. Mirrors app/services/erp/vocabulary.py (verify_arch47 T2).
OBJECT_KINDS = ("VENDOR_BILL", "PURCHASE_ORDER", "GOODS_RECEIPT", "JOURNAL_ENTRY", "PAYMENT_REFERENCE")
SOURCE_KINDS = ("PROCUREMENT_CASE", "TABLE", "CASE")
FORMATS = ("CSV", "XLSX", "X12", "UBL", "TALLY", "JSON")
TRANSPORTS = ("DOWNLOAD", "SFTP", "HTTP")
PRESETS = ("NONE", "QUICKBOOKS_ONLINE", "ZOHO_BOOKS", "BUSINESS_CENTRAL", "S4HANA_ODATA", "NETSUITE_REST",
           "GENERIC_REST", "GENERIC_ODATA")
ACK_MODES = ("SYNC", "X12_997", "ACK_FILE", "DELIVERY", "MANUAL")
AUTH_MODES = ("NONE", "BEARER", "BASIC", "API_KEY_HEADER", "OAUTH2_REFRESH_TOKEN", "OAUTH2_CLIENT_CREDENTIALS",
              "SSH_PASSWORD", "SSH_KEY")
TARGET_STATUSES = ("ACTIVE", "DISABLED")
MAPPING_STATUSES = ("ACTIVE", "RETIRED")
STATES = ("PENDING", "SENDING", "RETRYING", "DELIVERED", "DONE", "FAILED", "REJECTED", "MISMATCH", "UNCERTAIN",
          "CANCELLED")
OPEN_STATES = ("PENDING", "SENDING", "RETRYING", "DELIVERED")
EXCEPTION_STATES = ("FAILED", "REJECTED", "MISMATCH", "UNCERTAIN")
ORIGINS = ("MANUAL", "AUTO", "FLOW")
ATTEMPT_KINDS = ("RENDER", "SEND", "PROBE", "ACK", "REVIEW")
ATTEMPT_OUTCOMES = ("OK", "TRANSIENT", "PERMANENT", "UNCERTAIN", "FOUND", "ABSENT", "PENDING", "ACCEPTED", "REJECTED",
                    "MISMATCH")

#: ARCH47-S1:review-kinds. ARCH-46's eight + POSTING.
REVIEW_KINDS = ("EXTRACTION", "ASSERTION", "ANOMALY", "MERGE", "SPLIT", "TABLE", "CORROBORATION", "OBLIGATION",
                "POSTING")
REVIEW_KINDS_BEFORE = ("EXTRACTION", "ASSERTION", "ANOMALY", "MERGE", "SPLIT", "TABLE", "CORROBORATION", "OBLIGATION")
#: ARCH47-S1:review-reasons. ARCH-46's twelve + POSTING_EXCEPTION.
REVIEW_REASONS = (
    "DISAGREEMENT", "ESCALATION", "CALIBRATION_HOLD", "AUTONOMY_AUDIT",
    "PENDING_REVIEW", "CLAUSE_TRIAGE", "ANOMALY", "ENTITY_MERGE", "PACKET_SPLIT", "TABLE_ARITHMETIC",
    "MATERIAL_DISCREPANCY", "OBLIGATION_UNCONFIRMED", "POSTING_EXCEPTION",
)
#: ARCH47-S1:trigger-events. The new INTERNAL trigger event.
NEW_TRIGGER_EVENTS = ("trigger.posting.failed",)

TABLES_IN_DROP_ORDER = ("erp_posting_attempts", "erp_postings", "erp_mappings", "erp_lookup_tables", "erp_targets")


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(f"{name}.py"))
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _step46():
    return _load("arch46_step1_obligations")


def internal_after_47() -> tuple[str, ...]:
    return tuple(_step46().internal_after_46()) + NEW_TRIGGER_EVENTS


def _in(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{x}'" for x in values)


def _array(values: tuple[str, ...]) -> str:
    return "ARRAY[" + _in(values) + "]::varchar[]"


#: ARCH47-S1:review-view-v8. ARCH-46's view, unchanged, plus a POSTING arm. A
#: posting that failed, was rejected, was acknowledged with different figures,
#: or whose outcome is unknown is OPEN until a person retries, accepts or
#: cancels it (the state then leaves the exception set); one the target refused
#: or contradicted is HIGH.
POSTING_ARM = """
    UNION ALL

    SELECT
        'POSTING'::varchar(16),
        p.id,
        p.organization_id,
        p.workspace_id,
        p.work_item_id,
        (CASE p.state WHEN 'FAILED' THEN 'Posting failed: '
                      WHEN 'REJECTED' THEN 'Rejected by the target: '
                      WHEN 'MISMATCH' THEN 'Acknowledged differently: '
                      WHEN 'UNCERTAIN' THEN 'Posting outcome unknown: '
                      ELSE 'Posting: ' END
            || initcap(replace(p.object_kind, '_', ' '))
            || COALESCE(' ' || p.document_number, '') || ' to ' || t.name)::text,
        CASE WHEN p.state IN ('REJECTED', 'MISMATCH', 'UNCERTAIN') THEN 'HIGH' ELSE 'MEDIUM' END::varchar(8),
        CASE WHEN p.state IN ('REJECTED', 'MISMATCH', 'UNCERTAIN') THEN 2 ELSE 3 END::integer,
        NULL::numeric(5, 4),
        p.created_at,
        CASE WHEN p.state IN ('FAILED', 'REJECTED', 'MISMATCH', 'UNCERTAIN') THEN 'OPEN' ELSE 'RESOLVED' END::varchar(8),
        p.reviewed_at,
        p.reviewed_by_user_id,
        'POSTING_EXCEPTION'::varchar(24)
    FROM erp_postings p
    JOIN erp_targets t ON t.id = p.target_id
    WHERE p.state IN ('FAILED', 'REJECTED', 'MISMATCH', 'UNCERTAIN') OR p.reviewed_at IS NOT NULL
"""


def review_queue_view_v8() -> str:
    return _step46().review_queue_view_v7().rstrip() + "\n" + POSTING_ARM


def upgrade() -> None:
    # -- lookup tables must be flat string maps --------------------------------------
    op.execute(
        """
        CREATE FUNCTION erp_lookup_valid(entries jsonb) RETURNS boolean LANGUAGE sql IMMUTABLE AS $$
            SELECT jsonb_typeof(entries) = 'object'
               AND (SELECT count(*) FROM jsonb_each(entries)) <= 5000
               AND NOT EXISTS (SELECT 1 FROM jsonb_each(entries) e
                               WHERE jsonb_typeof(e.value) <> 'string'
                                  OR length(e.key) NOT BETWEEN 1 AND 200
                                  OR length(e.value #>> '{}') > 500)
        $$
        """
    )
    op.execute(
        f"""
        CREATE TABLE erp_targets (
            id uuid PRIMARY KEY,
            organization_id uuid NOT NULL,
            workspace_id uuid NOT NULL,
            name varchar(120) NOT NULL,
            format varchar(8) NOT NULL,
            transport varchar(10) NOT NULL,
            preset varchar(20) NOT NULL DEFAULT 'NONE',
            ack_mode varchar(10) NOT NULL,
            auth_mode varchar(28) NOT NULL DEFAULT 'NONE',
            status varchar(10) NOT NULL DEFAULT 'ACTIVE',
            config jsonb NOT NULL DEFAULT '{{}}'::jsonb,
            credential_ciphertext text NULL,
            credential_fingerprint varchar(12) NULL,
            credential_updated_at timestamptz NULL,
            auto_post boolean NOT NULL DEFAULT false,
            auto_post_since timestamptz NULL,
            auto_sources varchar(20)[] NOT NULL DEFAULT '{{}}',
            auto_objects varchar(20)[] NOT NULL DEFAULT '{{}}',
            max_attempts smallint NOT NULL DEFAULT 6,
            ack_timeout_hours integer NOT NULL DEFAULT 72,
            control_sequence bigint NOT NULL DEFAULT 0,
            revision integer NOT NULL DEFAULT 1,
            created_by_user_id uuid NULL REFERENCES users (id) ON DELETE SET NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT uq_erp_targets_id_workspace UNIQUE (id, workspace_id),
            CONSTRAINT uq_erp_targets_name UNIQUE (workspace_id, name),
            CONSTRAINT fk_erp_targets_workspace FOREIGN KEY (workspace_id, organization_id)
                REFERENCES workspaces (id, organization_id) ON DELETE CASCADE,
            CONSTRAINT ck_erp_targets_format CHECK (format IN ({_in(FORMATS)})),
            CONSTRAINT ck_erp_targets_transport CHECK (transport IN ({_in(TRANSPORTS)})),
            CONSTRAINT ck_erp_targets_preset CHECK (preset IN ({_in(PRESETS)})),
            CONSTRAINT ck_erp_targets_ack CHECK (ack_mode IN ({_in(ACK_MODES)})),
            CONSTRAINT ck_erp_targets_auth CHECK (auth_mode IN ({_in(AUTH_MODES)})),
            CONSTRAINT ck_erp_targets_status CHECK (status IN ({_in(TARGET_STATUSES)})),
            CONSTRAINT ck_erp_targets_json_http CHECK ((format = 'JSON') = (transport = 'HTTP')),
            CONSTRAINT ck_erp_targets_preset_json CHECK ((format = 'JSON') = (preset <> 'NONE')),
            CONSTRAINT ck_erp_targets_ack_transport CHECK (
                (transport = 'HTTP' AND ack_mode = 'SYNC')
                OR (transport = 'SFTP' AND ack_mode IN ('X12_997', 'ACK_FILE', 'DELIVERY'))
                OR (transport = 'DOWNLOAD' AND ack_mode = 'MANUAL')),
            CONSTRAINT ck_erp_targets_ack_997 CHECK (ack_mode <> 'X12_997' OR format = 'X12'),
            CONSTRAINT ck_erp_targets_auth_transport CHECK (
                (transport = 'HTTP' AND auth_mode IN ('BEARER', 'BASIC', 'API_KEY_HEADER', 'OAUTH2_REFRESH_TOKEN',
                                                      'OAUTH2_CLIENT_CREDENTIALS'))
                OR (transport = 'SFTP' AND auth_mode IN ('SSH_PASSWORD', 'SSH_KEY'))
                OR (transport = 'DOWNLOAD' AND auth_mode = 'NONE')),
            CONSTRAINT ck_erp_targets_credential CHECK ((credential_ciphertext IS NULL) = (credential_fingerprint IS NULL)
                AND (credential_fingerprint IS NULL OR credential_fingerprint ~ '^[0-9a-f]{{12}}$')
                AND (credential_ciphertext IS NULL OR credential_updated_at IS NOT NULL)),
            CONSTRAINT ck_erp_targets_no_credential_download CHECK (transport <> 'DOWNLOAD' OR credential_ciphertext IS NULL),
            CONSTRAINT ck_erp_targets_auto CHECK (NOT auto_post OR auto_post_since IS NOT NULL),
            CONSTRAINT ck_erp_targets_auto_sources CHECK (auto_sources <@ {_array(SOURCE_KINDS)}),
            CONSTRAINT ck_erp_targets_auto_objects CHECK (auto_objects <@ {_array(OBJECT_KINDS)}),
            CONSTRAINT ck_erp_targets_attempts CHECK (max_attempts BETWEEN 1 AND 10),
            CONSTRAINT ck_erp_targets_ack_timeout CHECK (ack_timeout_hours BETWEEN 1 AND 720),
            CONSTRAINT ck_erp_targets_sequence CHECK (control_sequence BETWEEN 0 AND 999999999),
            CONSTRAINT ck_erp_targets_name_length CHECK (length(btrim(name)) BETWEEN 1 AND 120),
            CONSTRAINT ck_erp_targets_config CHECK (jsonb_typeof(config) = 'object'),
            CONSTRAINT ck_erp_targets_revision CHECK (revision >= 1)
        )
        """
    )
    op.execute("CREATE INDEX ix_erp_targets_auto ON erp_targets (workspace_id) WHERE auto_post AND status = 'ACTIVE'")

    op.execute(
        f"""
        CREATE TABLE erp_mappings (
            id uuid PRIMARY KEY,
            organization_id uuid NOT NULL,
            workspace_id uuid NOT NULL,
            target_id uuid NOT NULL,
            object_kind varchar(20) NOT NULL,
            version integer NOT NULL,
            status varchar(10) NOT NULL DEFAULT 'ACTIVE',
            spec jsonb NOT NULL,
            spec_sha char(64) NOT NULL,
            note varchar(300) NULL,
            created_by_user_id uuid NULL REFERENCES users (id) ON DELETE SET NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            retired_at timestamptz NULL,
            CONSTRAINT uq_erp_mappings_id_workspace UNIQUE (id, workspace_id),
            CONSTRAINT uq_erp_mappings_version UNIQUE (target_id, object_kind, version),
            CONSTRAINT fk_erp_mappings_target FOREIGN KEY (target_id, workspace_id)
                REFERENCES erp_targets (id, workspace_id) ON DELETE CASCADE,
            CONSTRAINT ck_erp_mappings_object CHECK (object_kind IN ({_in(OBJECT_KINDS)})),
            CONSTRAINT ck_erp_mappings_status CHECK (status IN ({_in(MAPPING_STATUSES)})),
            CONSTRAINT ck_erp_mappings_retired CHECK ((status = 'RETIRED') = (retired_at IS NOT NULL)),
            CONSTRAINT ck_erp_mappings_version CHECK (version >= 1),
            CONSTRAINT ck_erp_mappings_spec CHECK (jsonb_typeof(spec) = 'object' AND octet_length(spec::text) <= 65536),
            CONSTRAINT ck_erp_mappings_sha CHECK (spec_sha ~ '^[0-9a-f]{{64}}$')
        )
        """
    )
    op.execute("CREATE UNIQUE INDEX uq_erp_mappings_active ON erp_mappings (target_id, object_kind) "
               "WHERE status = 'ACTIVE'")

    op.execute(
        """
        CREATE TABLE erp_lookup_tables (
            id uuid PRIMARY KEY,
            organization_id uuid NOT NULL,
            workspace_id uuid NOT NULL,
            name varchar(64) NOT NULL,
            description varchar(300) NULL,
            entries jsonb NOT NULL DEFAULT '{}'::jsonb,
            revision integer NOT NULL DEFAULT 1,
            created_by_user_id uuid NULL REFERENCES users (id) ON DELETE SET NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT uq_erp_lookup_tables_name UNIQUE (workspace_id, name),
            CONSTRAINT fk_erp_lookup_tables_workspace FOREIGN KEY (workspace_id, organization_id)
                REFERENCES workspaces (id, organization_id) ON DELETE CASCADE,
            CONSTRAINT ck_erp_lookup_tables_name CHECK (name ~ '^[a-z][a-z0-9_]{0,63}$'),
            CONSTRAINT ck_erp_lookup_tables_entries CHECK (erp_lookup_valid(entries)),
            CONSTRAINT ck_erp_lookup_tables_revision CHECK (revision >= 1)
        )
        """
    )

    op.execute(
        f"""
        CREATE TABLE erp_postings (
            id uuid PRIMARY KEY,
            organization_id uuid NOT NULL,
            workspace_id uuid NOT NULL,
            target_id uuid NOT NULL,
            mapping_id uuid NULL,
            mapping_version integer NULL,
            object_kind varchar(20) NOT NULL,
            source_kind varchar(20) NOT NULL,
            source_id uuid NOT NULL,
            work_item_id uuid NULL,
            origin varchar(8) NOT NULL,
            state varchar(10) NOT NULL DEFAULT 'PENDING',
            idempotency_key char(64) NOT NULL,
            source_digest char(64) NULL,
            engine_version varchar(16) NOT NULL,
            document_number varchar(100) NULL,
            amount numeric(20, 6) NULL,
            currency char(3) NULL,
            canonical jsonb NULL,
            mapped jsonb NULL,
            rendered bytea NULL,
            rendered_media_type varchar(100) NULL,
            rendered_filename varchar(200) NULL,
            content_sha char(64) NULL,
            attempts integer NOT NULL DEFAULT 0,
            max_attempts integer NOT NULL DEFAULT 6,
            next_attempt_at timestamptz NULL,
            lease_until timestamptz NULL,
            lease_token uuid NULL,
            external_id varchar(200) NULL,
            ack jsonb NOT NULL DEFAULT '{{}}'::jsonb,
            control_numbers jsonb NOT NULL DEFAULT '{{}}'::jsonb,
            remote_path varchar(500) NULL,
            last_error text NULL,
            exception_seq integer NOT NULL DEFAULT 0,
            delivered_at timestamptz NULL,
            acknowledged_at timestamptz NULL,
            cancelled_at timestamptz NULL,
            reviewed_at timestamptz NULL,
            reviewed_by_user_id uuid NULL REFERENCES users (id) ON DELETE SET NULL,
            review_note varchar(500) NULL,
            erased_at timestamptz NULL,
            created_by_user_id uuid NULL REFERENCES users (id) ON DELETE SET NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT uq_erp_postings_id_workspace UNIQUE (id, workspace_id),
            CONSTRAINT uq_erp_postings_ledger UNIQUE (target_id, object_kind, source_kind, source_id),
            CONSTRAINT uq_erp_postings_idempotency UNIQUE (idempotency_key),
            CONSTRAINT fk_erp_postings_workspace FOREIGN KEY (workspace_id, organization_id)
                REFERENCES workspaces (id, organization_id) ON DELETE CASCADE,
            CONSTRAINT fk_erp_postings_target FOREIGN KEY (target_id, workspace_id)
                REFERENCES erp_targets (id, workspace_id),
            CONSTRAINT fk_erp_postings_mapping FOREIGN KEY (mapping_id, workspace_id)
                REFERENCES erp_mappings (id, workspace_id) ON DELETE SET NULL (mapping_id),
            CONSTRAINT fk_erp_postings_work_item FOREIGN KEY (work_item_id, workspace_id)
                REFERENCES work_items (id, workspace_id) ON DELETE SET NULL (work_item_id),
            CONSTRAINT ck_erp_postings_object CHECK (object_kind IN ({_in(OBJECT_KINDS)})),
            CONSTRAINT ck_erp_postings_source CHECK (source_kind IN ({_in(SOURCE_KINDS)})),
            CONSTRAINT ck_erp_postings_origin CHECK (origin IN ({_in(ORIGINS)})),
            CONSTRAINT ck_erp_postings_state CHECK (state IN ({_in(STATES)})),
            CONSTRAINT ck_erp_postings_keys CHECK (idempotency_key ~ '^[0-9a-f]{{64}}$'
                AND (source_digest IS NULL OR source_digest ~ '^[0-9a-f]{{64}}$')
                AND (content_sha IS NULL OR content_sha ~ '^[0-9a-f]{{64}}$')),
            CONSTRAINT ck_erp_postings_rendered CHECK (state NOT IN ({_in(OPEN_STATES)})
                OR (rendered IS NOT NULL AND content_sha IS NOT NULL AND rendered_filename IS NOT NULL)
                OR (erased_at IS NOT NULL AND state IN ('SENDING', 'DELIVERED'))),
            CONSTRAINT ck_erp_postings_sending CHECK ((state = 'SENDING') = (lease_token IS NOT NULL)
                AND (state <> 'SENDING' OR lease_until IS NOT NULL)),
            CONSTRAINT ck_erp_postings_retrying CHECK (state <> 'RETRYING' OR next_attempt_at IS NOT NULL),
            CONSTRAINT ck_erp_postings_delivered CHECK (state NOT IN ('DELIVERED', 'DONE') OR delivered_at IS NOT NULL
                OR (state = 'DONE' AND reviewed_at IS NOT NULL)),
            CONSTRAINT ck_erp_postings_done CHECK ((state = 'DONE') = (acknowledged_at IS NOT NULL)),
            CONSTRAINT ck_erp_postings_cancelled CHECK ((state = 'CANCELLED') = (cancelled_at IS NOT NULL)),
            CONSTRAINT ck_erp_postings_attempts CHECK (attempts BETWEEN 0 AND 100 AND max_attempts BETWEEN 1 AND 100),
            CONSTRAINT ck_erp_postings_exceptions CHECK (exception_seq >= 0
                AND (state NOT IN ({_in(EXCEPTION_STATES)}) OR exception_seq >= 1)),
            CONSTRAINT ck_erp_postings_erased CHECK (erased_at IS NULL OR (rendered IS NULL AND canonical IS NULL
                AND mapped IS NULL)),
            CONSTRAINT ck_erp_postings_currency CHECK (currency IS NULL OR currency ~ '^[A-Z]{{3}}$'),
            CONSTRAINT ck_erp_postings_mapping_version CHECK ((mapping_id IS NULL) OR mapping_version IS NOT NULL),
            CONSTRAINT ck_erp_postings_json CHECK (jsonb_typeof(ack) = 'object' AND jsonb_typeof(control_numbers) = 'object'
                AND (canonical IS NULL OR jsonb_typeof(canonical) = 'object')
                AND (mapped IS NULL OR jsonb_typeof(mapped) = 'object'))
        )
        """
    )
    op.execute("CREATE INDEX ix_erp_postings_workspace ON erp_postings (workspace_id, created_at DESC)")
    op.execute("CREATE INDEX ix_erp_postings_due ON erp_postings (next_attempt_at) WHERE state = 'RETRYING'")
    op.execute("CREATE INDEX ix_erp_postings_waiting ON erp_postings (target_id, delivered_at) WHERE state = 'DELIVERED'")
    op.execute("CREATE INDEX ix_erp_postings_sending ON erp_postings (lease_until) WHERE state = 'SENDING'")
    op.execute("CREATE INDEX ix_erp_postings_source ON erp_postings (source_kind, source_id)")
    op.execute("CREATE INDEX ix_erp_postings_work_item ON erp_postings (work_item_id) WHERE work_item_id IS NOT NULL")
    op.execute(f"CREATE INDEX ix_erp_postings_exceptions ON erp_postings (workspace_id) "
               f"WHERE state IN ({_in(EXCEPTION_STATES)})")

    op.execute(
        f"""
        CREATE TABLE erp_posting_attempts (
            id uuid PRIMARY KEY,
            posting_id uuid NOT NULL,
            workspace_id uuid NOT NULL,
            seq integer NOT NULL,
            kind varchar(8) NOT NULL,
            outcome varchar(10) NOT NULL,
            http_status integer NULL,
            message text NULL,
            detail jsonb NOT NULL DEFAULT '{{}}'::jsonb,
            actor_user_id uuid NULL REFERENCES users (id) ON DELETE SET NULL,
            started_at timestamptz NOT NULL DEFAULT now(),
            finished_at timestamptz NULL,
            CONSTRAINT uq_erp_posting_attempts_seq UNIQUE (posting_id, seq),
            CONSTRAINT fk_erp_posting_attempts_posting FOREIGN KEY (posting_id, workspace_id)
                REFERENCES erp_postings (id, workspace_id) ON DELETE CASCADE,
            CONSTRAINT ck_erp_posting_attempts_kind CHECK (kind IN ({_in(ATTEMPT_KINDS)})),
            CONSTRAINT ck_erp_posting_attempts_outcome CHECK (outcome IN ({_in(ATTEMPT_OUTCOMES)})),
            CONSTRAINT ck_erp_posting_attempts_seq CHECK (seq >= 1),
            CONSTRAINT ck_erp_posting_attempts_status CHECK (http_status IS NULL OR http_status BETWEEN 100 AND 599),
            CONSTRAINT ck_erp_posting_attempts_json CHECK (jsonb_typeof(detail) = 'object'),
            CONSTRAINT ck_erp_posting_attempts_time CHECK (finished_at IS NULL OR finished_at >= started_at)
        )
        """
    )

    _visibility_check(internal_after_47())
    # -- the review hub learns POSTING -------------------------------------------------
    op.execute("ALTER TABLE review_assignments DROP CONSTRAINT ck_review_assignments_kind_known")
    op.execute(
        "ALTER TABLE review_assignments ADD CONSTRAINT ck_review_assignments_kind_known "
        f"CHECK (kind IN ({_in(REVIEW_KINDS)}))"
    )
    op.execute("DROP VIEW IF EXISTS review_queue_items")
    op.execute(review_queue_view_v8())


def _visibility_check(values: tuple[str, ...]) -> None:
    """ARCH-37's constraint, same shape byte for byte (arch40_step0._visibility_check)."""
    _load("arch40_step0_review_vocabulary")._visibility_check(values)


def downgrade() -> None:
    op.execute("DELETE FROM outbox_events WHERE event_type IN ('trigger.posting.failed')")
    _visibility_check(tuple(_step46().internal_after_46()))
    op.execute("DROP VIEW IF EXISTS review_queue_items")
    op.execute(_step46().review_queue_view_v7())
    op.execute("DELETE FROM review_assignments WHERE kind = 'POSTING'")
    op.execute("ALTER TABLE review_assignments DROP CONSTRAINT ck_review_assignments_kind_known")
    op.execute(
        "ALTER TABLE review_assignments ADD CONSTRAINT ck_review_assignments_kind_known "
        f"CHECK (kind IN ({_in(REVIEW_KINDS_BEFORE)}))"
    )
    for table in TABLES_IN_DROP_ORDER:
        op.execute(f"DROP TABLE IF EXISTS {table}")
    op.execute("DROP FUNCTION IF EXISTS erp_lookup_valid(jsonb)")

"""ARCH-43 Step 1 — EXPAND: fair claiming, the packet dicer, lineage and case intelligence.

Revision ID: arch43_step1_case_intelligence
Revises: arch42_step1_entity_graph

ARCH43-S1:migration. Expand only. The whole of ARCH-43 (Universal Packet
Dicer & Case Intelligence) in one revision: nothing existing is dropped or
narrowed.

CHAIN POSITION
==============

Inserted between arch42_step1_entity_graph (the ARCH-42 release head) and
arch40_step3_contract_ai_settings (the flag-gated, lossy contract step), as
hm1, ARCH-41 and ARCH-42 were: the contract step now revises this revision, so
there is still ONE file head and `alembic upgrade head` still refuses the
contract without ARCH40_CONTRACT. run_arch43.ps1 upgrades to this revision by
name and handles a database whose contract already ran.

WHAT IT ADDS
============

  tenant_queue_weights     weighted per-organization fair claiming (roadmap
                           adjustment 2). Absent row = weight 1. Set by the
                           platform operator (scripts/queue_weights.py), never
                           by a tenant.
  ix_jobs_claimable_by_org
  ix_jobs_inflight_by_org  the two partial indexes the fair claim reads.
  work_items lineage       parent_work_item_id + parent_page_start/end, a
                           composite FK onto (id, workspace_id) so a child can
                           never name another workspace's packet. Erasing the
                           packet nulls only the pointer (PG15+ column list);
                           the page range stays as provenance.
  packet_splits            one plan per packet: status, model version, the
                           per-page boundary probabilities and features.
  packet_split_segments    the plan's documents: page ranges with CHECKs, a
                           generated int4range and an EXCLUSION constraint
                           (btree_gist) so no two segments of a plan overlap;
                           a trigger refuses a range past the packet's end.
  entity_mentions          superseded_at / superseded_by_split_id: when a
                           packet is split, the parent's mentions are
                           SUPERSEDED (roadmap adjustment 4), never deleted.
  case_templates           required document types + consistency rules
                           (EQUAL, FUZZY_EQUAL, DATE_ORDER, WITHIN_DAYS,
                           SUM_EQUALS). IMMUTABLE ONCE PUBLISHED, by trigger:
                           a published version can only be retired; a change
                           is a new version.
  cases / case_documents   assembled by ARCH-42 entity root or ARCH-38 batch
  case_rule_results        (or by hand); one live case per template+anchor.
  document_requests        missing-document requests: only the SHA-256 of the
                           single-use token is stored; consuming it is one
                           conditional UPDATE, so it cannot be used twice.
  outbox vocabulary        trigger.packet.split, trigger.case.completed and
                           trigger.case.inconsistent become legal INTERNAL
                           events (the ARCH-37 CHECK is rebuilt, same shape).
  review hub               a fifth kind, SPLIT (reason PACKET_SPLIT): the
                           CHECK is recreated and the view is ARCH-42's text
                           plus one arm (loaded, not copied).
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

from alembic import op

revision = "arch43_step1_case_intelligence"
down_revision = "arch42_step1_entity_graph"
branch_labels = None
depends_on = None

SPLIT_STATUSES = ("PROPOSED", "SINGLE", "APPROVED", "APPLYING", "APPLIED", "REJECTED", "FAILED", "SUPERSEDED")
LIVE_STATUSES = ("PROPOSED", "SINGLE", "APPROVED", "APPLYING", "APPLIED")
SPLIT_SOURCES = ("MODEL", "REVIEWER")

#: ARCH43-S1:review-kinds. ARCH-42's four + SPLIT.
REVIEW_KINDS = ("EXTRACTION", "ASSERTION", "ANOMALY", "MERGE", "SPLIT")
REVIEW_KINDS_BEFORE = ("EXTRACTION", "ASSERTION", "ANOMALY", "MERGE")
#: ARCH43-S1:review-reasons. ARCH-42's eight + PACKET_SPLIT.
REVIEW_REASONS = (
    "DISAGREEMENT", "ESCALATION", "CALIBRATION_HOLD", "AUTONOMY_AUDIT",
    "PENDING_REVIEW", "CLAUSE_TRIAGE", "ANOMALY", "ENTITY_MERGE", "PACKET_SPLIT",
)

TABLES_IN_DROP_ORDER = ("document_requests", "case_rule_results", "case_documents", "cases", "case_templates",
                        "packet_split_segments", "packet_splits", "tenant_queue_weights")

TEMPLATE_STATUSES = ("DRAFT", "PUBLISHED", "RETIRED")
ASSEMBLY_KEYS = ("ENTITY", "BATCH", "MANUAL")
CASE_STATUSES = ("INCOMPLETE", "INCONSISTENT", "COMPLETE", "CLOSED")
RULE_OUTCOMES = ("PASS", "FAIL", "MISSING", "ERROR")
DOCUMENT_SOURCES = ("AUTO", "MANUAL", "REQUEST")
REQUEST_STATUSES = ("OPEN", "FULFILLED", "EXPIRED", "REVOKED")
#: ARCH43-S1:trigger-events. The three new INTERNAL trigger events.
NEW_TRIGGER_EVENTS = ("trigger.packet.split", "trigger.case.completed", "trigger.case.inconsistent")


def _step40_0():
    spec = importlib.util.spec_from_file_location(
        "arch40_step0_review_vocabulary", Path(__file__).with_name("arch40_step0_review_vocabulary.py"))
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def internal_after_43() -> tuple[str, ...]:
    return tuple(_step40_0().INTERNAL_AFTER_40) + NEW_TRIGGER_EVENTS


def _in(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{x}'" for x in values)


def _step42():
    spec = importlib.util.spec_from_file_location(
        "arch42_step1_entity_graph", Path(__file__).with_name("arch42_step1_entity_graph.py"))
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


#: ARCH43-S1:review-view-v4. ARCH-42's view (itself ARCH-40's text + MERGE),
#: unchanged, plus a SPLIT arm. A plan is OPEN while PROPOSED; the reviewer's
#: decision (APPROVED onwards, or REJECTED) resolves it.
SPLIT_ARM = """
    UNION ALL

    SELECT
        'SPLIT'::varchar(16),
        ps.id,
        ps.organization_id,
        ps.workspace_id,
        ps.work_item_id,
        ('Split ' || wi.original_filename || ' (' || ps.page_count || ' pages) into '
            || (SELECT count(*) FROM packet_split_segments s WHERE s.split_id = ps.id) || ' documents?')::text,
        CASE WHEN ps.certainty < 0.8 THEN 'MEDIUM' ELSE 'LOW' END::varchar(8),
        CASE WHEN ps.certainty < 0.8 THEN 3 ELSE 4 END::integer,
        ps.certainty,
        ps.proposed_at,
        CASE WHEN ps.status = 'PROPOSED' THEN 'OPEN' ELSE 'RESOLVED' END::varchar(8),
        ps.decided_at,
        ps.decided_by_user_id,
        'PACKET_SPLIT'::varchar(24)
    FROM packet_splits ps
    JOIN work_items wi ON wi.id = ps.work_item_id
    WHERE ps.status IN ('PROPOSED', 'APPROVED', 'APPLYING', 'APPLIED', 'REJECTED')
"""


def review_queue_view_v4() -> str:
    return _step42().review_queue_view_v3().rstrip() + "\n" + SPLIT_ARM


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS btree_gist")

    # -- Tranche 1: weighted per-organization fair claiming --------------------
    op.execute(
        """
        CREATE TABLE tenant_queue_weights (
            organization_id uuid PRIMARY KEY REFERENCES organizations (id) ON DELETE CASCADE,
            weight numeric(6, 2) NOT NULL DEFAULT 1,
            max_inflight integer NULL,
            note text NULL,
            updated_by varchar(128) NULL,
            updated_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT ck_tenant_queue_weights_weight CHECK (weight >= 0.1 AND weight <= 100),
            CONSTRAINT ck_tenant_queue_weights_inflight CHECK (max_inflight IS NULL OR max_inflight >= 1)
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_jobs_claimable_by_org ON jobs (organization_id, available_at, seq) "
        "WHERE status IN ('PENDING'::job_status, 'FAILED'::job_status)"
    )
    op.execute("CREATE INDEX ix_jobs_inflight_by_org ON jobs (organization_id) WHERE status = 'CLAIMED'::job_status")

    # -- Tranche 2: lineage ------------------------------------------------------
    op.execute(
        "ALTER TABLE work_items ADD COLUMN parent_work_item_id uuid NULL, "
        "ADD COLUMN parent_page_start integer NULL, ADD COLUMN parent_page_end integer NULL"
    )
    op.execute(
        "ALTER TABLE work_items ADD CONSTRAINT fk_work_items_parent FOREIGN KEY (parent_work_item_id, workspace_id) "
        "REFERENCES work_items (id, workspace_id) ON DELETE SET NULL (parent_work_item_id)"
    )
    op.execute(
        "ALTER TABLE work_items ADD CONSTRAINT ck_work_items_parent_pages CHECK ("
        "(parent_page_start IS NULL) = (parent_page_end IS NULL) "
        "AND (parent_page_start IS NULL OR (parent_page_start >= 1 AND parent_page_end >= parent_page_start)) "
        "AND (parent_work_item_id IS NULL OR parent_page_start IS NOT NULL))"
    )
    op.execute(
        "ALTER TABLE work_items ADD CONSTRAINT ck_work_items_not_own_parent "
        "CHECK (parent_work_item_id IS NULL OR parent_work_item_id <> id)"
    )
    op.execute("CREATE INDEX ix_work_items_parent ON work_items (parent_work_item_id) WHERE parent_work_item_id IS NOT NULL")

    # -- Tranche 2: the packet dicer ---------------------------------------------
    op.execute(
        f"""
        CREATE TABLE packet_splits (
            id uuid PRIMARY KEY,
            organization_id uuid NOT NULL,
            workspace_id uuid NOT NULL,
            work_item_id uuid NOT NULL,
            status varchar(12) NOT NULL,
            source varchar(10) NOT NULL DEFAULT 'MODEL',
            page_count integer NOT NULL,
            model_version varchar(40) NOT NULL,
            threshold numeric(5, 4) NOT NULL,
            certainty numeric(5, 4) NULL,
            page_scores jsonb NOT NULL DEFAULT '[]'::jsonb,
            failure_reason text NULL,
            proposed_at timestamptz NOT NULL DEFAULT now(),
            created_by_user_id uuid NULL REFERENCES users (id) ON DELETE SET NULL,
            decided_at timestamptz NULL,
            decided_by_user_id uuid NULL REFERENCES users (id) ON DELETE SET NULL,
            applied_at timestamptz NULL,
            updated_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT uq_packet_splits_id_workspace UNIQUE (id, workspace_id),
            CONSTRAINT fk_packet_splits_workspace FOREIGN KEY (workspace_id, organization_id)
                REFERENCES workspaces (id, organization_id) ON DELETE CASCADE,
            CONSTRAINT fk_packet_splits_work_item FOREIGN KEY (work_item_id, workspace_id)
                REFERENCES work_items (id, workspace_id) ON DELETE CASCADE,
            CONSTRAINT ck_packet_splits_status CHECK (status IN ({_in(SPLIT_STATUSES)})),
            CONSTRAINT ck_packet_splits_source CHECK (source IN ({_in(SPLIT_SOURCES)})),
            CONSTRAINT ck_packet_splits_page_count CHECK (page_count >= 1),
            CONSTRAINT ck_packet_splits_threshold CHECK (threshold > 0 AND threshold < 1),
            CONSTRAINT ck_packet_splits_certainty CHECK (certainty IS NULL OR (certainty >= 0 AND certainty <= 1)),
            CONSTRAINT ck_packet_splits_scores_array CHECK (jsonb_typeof(page_scores) = 'array'),
            CONSTRAINT ck_packet_splits_decided CHECK (
                status NOT IN ('APPROVED', 'APPLYING', 'APPLIED', 'REJECTED') OR decided_at IS NOT NULL),
            CONSTRAINT ck_packet_splits_applied CHECK ((status = 'APPLIED') = (applied_at IS NOT NULL))
        )
        """
    )
    op.execute(
        f"CREATE UNIQUE INDEX uq_packet_splits_live ON packet_splits (work_item_id) WHERE status IN ({_in(LIVE_STATUSES)})"
    )
    op.execute("CREATE INDEX ix_packet_splits_workspace_status ON packet_splits (workspace_id, status, proposed_at DESC)")
    op.execute(
        """
        CREATE TABLE packet_split_segments (
            id uuid PRIMARY KEY,
            split_id uuid NOT NULL,
            workspace_id uuid NOT NULL,
            ordinal integer NOT NULL,
            page_start integer NOT NULL,
            page_end integer NOT NULL,
            pages int4range GENERATED ALWAYS AS (int4range(page_start, page_end, '[]')) STORED,
            document_type varchar(64) NULL,
            confidence numeric(5, 4) NULL,
            title varchar(200) NULL,
            child_work_item_id uuid NULL,
            CONSTRAINT fk_packet_split_segments_split FOREIGN KEY (split_id, workspace_id)
                REFERENCES packet_splits (id, workspace_id) ON DELETE CASCADE,
            CONSTRAINT fk_packet_split_segments_child FOREIGN KEY (child_work_item_id, workspace_id)
                REFERENCES work_items (id, workspace_id) ON DELETE SET NULL (child_work_item_id),
            CONSTRAINT ck_packet_split_segments_pages CHECK (page_start >= 1 AND page_end >= page_start),
            CONSTRAINT ck_packet_split_segments_ordinal CHECK (ordinal >= 0),
            CONSTRAINT ck_packet_split_segments_confidence CHECK (confidence IS NULL OR (confidence >= 0 AND confidence <= 1)),
            CONSTRAINT uq_packet_split_segments_ordinal UNIQUE (split_id, ordinal),
            CONSTRAINT ex_packet_split_segments_no_overlap EXCLUDE USING gist (split_id WITH =, pages WITH &&)
        )
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_packet_split_segments_child ON packet_split_segments (child_work_item_id) "
        "WHERE child_work_item_id IS NOT NULL"
    )
    op.execute(
        """
        CREATE FUNCTION packet_split_segment_within_packet() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE pages integer;
        BEGIN
            SELECT page_count INTO pages FROM packet_splits WHERE id = NEW.split_id;
            IF pages IS NOT NULL AND NEW.page_end > pages THEN
                RAISE EXCEPTION 'segment pages %-% exceed the packet''s % pages', NEW.page_start, NEW.page_end, pages
                    USING ERRCODE = '23514', CONSTRAINT = 'ck_packet_split_segments_within_packet';
            END IF;
            RETURN NEW;
        END $$
        """
    )
    op.execute(
        "CREATE TRIGGER trg_packet_split_segments_within_packet BEFORE INSERT OR UPDATE ON packet_split_segments "
        "FOR EACH ROW EXECUTE FUNCTION packet_split_segment_within_packet()"
    )

    # -- Tranche 2: lineage re-resolution (supersede, never delete) --------------
    op.execute(
        "ALTER TABLE entity_mentions ADD COLUMN superseded_at timestamptz NULL, "
        "ADD COLUMN superseded_by_split_id uuid NULL"
    )
    op.execute(
        "ALTER TABLE entity_mentions ADD CONSTRAINT fk_entity_mentions_superseded_by FOREIGN KEY "
        "(superseded_by_split_id, workspace_id) REFERENCES packet_splits (id, workspace_id) "
        "ON DELETE SET NULL (superseded_by_split_id)"
    )
    op.execute(
        "ALTER TABLE entity_mentions ADD CONSTRAINT ck_entity_mentions_superseded "
        "CHECK (superseded_by_split_id IS NULL OR superseded_at IS NOT NULL)"
    )
    op.execute("CREATE INDEX ix_entity_mentions_live ON entity_mentions (entity_id) WHERE superseded_at IS NULL")

    # -- Tranche 3: case intelligence -------------------------------------------
    op.execute(
        f"""
        CREATE TABLE case_templates (
            id uuid PRIMARY KEY,
            organization_id uuid NOT NULL,
            workspace_id uuid NOT NULL,
            key varchar(64) NOT NULL,
            version integer NOT NULL DEFAULT 1,
            name varchar(160) NOT NULL,
            description text NOT NULL DEFAULT '',
            status varchar(10) NOT NULL DEFAULT 'DRAFT',
            assembly_key varchar(8) NOT NULL,
            entity_kind varchar(16) NULL,
            entity_role varchar(48) NULL,
            required_documents jsonb NOT NULL DEFAULT '[]'::jsonb,
            rules jsonb NOT NULL DEFAULT '[]'::jsonb,
            request_ttl_hours integer NOT NULL DEFAULT 72,
            created_by_user_id uuid NULL REFERENCES users (id) ON DELETE SET NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            published_at timestamptz NULL,
            retired_at timestamptz NULL,
            CONSTRAINT uq_case_templates_id_workspace UNIQUE (id, workspace_id),
            CONSTRAINT uq_case_templates_key_version UNIQUE (workspace_id, key, version),
            CONSTRAINT fk_case_templates_workspace FOREIGN KEY (workspace_id, organization_id)
                REFERENCES workspaces (id, organization_id) ON DELETE CASCADE,
            CONSTRAINT ck_case_templates_status CHECK (status IN ({_in(TEMPLATE_STATUSES)})),
            CONSTRAINT ck_case_templates_assembly CHECK (assembly_key IN ({_in(ASSEMBLY_KEYS)})),
            CONSTRAINT ck_case_templates_entity_kind CHECK ((assembly_key = 'ENTITY') = (entity_kind IS NOT NULL)),
            CONSTRAINT ck_case_templates_key CHECK (key ~ '^[a-z0-9][a-z0-9_\\-]{{1,63}}$'),
            CONSTRAINT ck_case_templates_version CHECK (version >= 1),
            CONSTRAINT ck_case_templates_required_array CHECK (jsonb_typeof(required_documents) = 'array'),
            CONSTRAINT ck_case_templates_rules_array CHECK (jsonb_typeof(rules) = 'array'),
            CONSTRAINT ck_case_templates_ttl CHECK (request_ttl_hours BETWEEN 1 AND 2160),
            CONSTRAINT ck_case_templates_published CHECK ((status = 'DRAFT') = (published_at IS NULL))
        )
        """
    )
    op.execute(
        """
        CREATE FUNCTION case_templates_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                IF OLD.status <> 'DRAFT' THEN
                    RAISE EXCEPTION 'case template %% v%% is published and cannot be deleted', OLD.key, OLD.version
                        USING ERRCODE = '23514', CONSTRAINT = 'ck_case_templates_immutable';
                END IF;
                RETURN OLD;
            END IF;
            IF OLD.status <> 'DRAFT' AND (
                NEW.key, NEW.version, NEW.name, NEW.description, NEW.assembly_key, NEW.entity_kind, NEW.entity_role,
                NEW.required_documents, NEW.rules, NEW.request_ttl_hours, NEW.published_at, NEW.workspace_id
            ) IS DISTINCT FROM (
                OLD.key, OLD.version, OLD.name, OLD.description, OLD.assembly_key, OLD.entity_kind, OLD.entity_role,
                OLD.required_documents, OLD.rules, OLD.request_ttl_hours, OLD.published_at, OLD.workspace_id
            ) THEN
                RAISE EXCEPTION 'case template %% v%% is published and immutable; publish a new version', OLD.key, OLD.version
                    USING ERRCODE = '23514', CONSTRAINT = 'ck_case_templates_immutable';
            END IF;
            IF OLD.status = 'RETIRED' AND NEW.status <> 'RETIRED' THEN
                RAISE EXCEPTION 'a retired case template cannot be revived'
                    USING ERRCODE = '23514', CONSTRAINT = 'ck_case_templates_immutable';
            END IF;
            IF OLD.status = 'PUBLISHED' AND NEW.status = 'DRAFT' THEN
                RAISE EXCEPTION 'a published case template cannot return to draft'
                    USING ERRCODE = '23514', CONSTRAINT = 'ck_case_templates_immutable';
            END IF;
            RETURN NEW;
        END $$
        """.replace("%%", "%")
    )
    op.execute(
        "CREATE TRIGGER trg_case_templates_immutable BEFORE UPDATE OR DELETE ON case_templates "
        "FOR EACH ROW EXECUTE FUNCTION case_templates_immutable()"
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_case_templates_one_published ON case_templates (workspace_id, key) WHERE status = 'PUBLISHED'"
    )
    op.execute(
        f"""
        CREATE TABLE cases (
            id uuid PRIMARY KEY,
            organization_id uuid NOT NULL,
            workspace_id uuid NOT NULL,
            template_id uuid NOT NULL,
            anchor_kind varchar(8) NOT NULL,
            anchor_entity_id uuid NULL,
            anchor_batch_id uuid NULL,
            title varchar(300) NOT NULL,
            status varchar(12) NOT NULL DEFAULT 'INCOMPLETE',
            completeness numeric(5, 4) NOT NULL DEFAULT 0,
            revision integer NOT NULL DEFAULT 0,
            created_by_user_id uuid NULL REFERENCES users (id) ON DELETE SET NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            evaluated_at timestamptz NULL,
            completed_at timestamptz NULL,
            closed_at timestamptz NULL,
            CONSTRAINT uq_cases_id_workspace UNIQUE (id, workspace_id),
            CONSTRAINT fk_cases_workspace FOREIGN KEY (workspace_id, organization_id)
                REFERENCES workspaces (id, organization_id) ON DELETE CASCADE,
            CONSTRAINT fk_cases_template FOREIGN KEY (template_id, workspace_id)
                REFERENCES case_templates (id, workspace_id) ON DELETE RESTRICT,
            CONSTRAINT fk_cases_anchor_entity FOREIGN KEY (anchor_entity_id, workspace_id)
                REFERENCES entities (id, workspace_id) ON DELETE SET NULL (anchor_entity_id),
            CONSTRAINT fk_cases_anchor_batch FOREIGN KEY (anchor_batch_id)
                REFERENCES ingestion_batches (id) ON DELETE SET NULL,
            CONSTRAINT ck_cases_anchor_kind CHECK (anchor_kind IN ({_in(ASSEMBLY_KEYS)})),
            CONSTRAINT ck_cases_status CHECK (status IN ({_in(CASE_STATUSES)})),
            CONSTRAINT ck_cases_completeness CHECK (completeness >= 0 AND completeness <= 1),
            CONSTRAINT ck_cases_closed CHECK ((status = 'CLOSED') = (closed_at IS NOT NULL)),
            CONSTRAINT ck_cases_anchor CHECK (
                (anchor_kind <> 'BATCH' OR anchor_entity_id IS NULL) AND (anchor_kind <> 'ENTITY' OR anchor_batch_id IS NULL)
                AND (anchor_kind <> 'MANUAL' OR (anchor_entity_id IS NULL AND anchor_batch_id IS NULL)))
        )
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_cases_live_entity ON cases (template_id, anchor_entity_id) "
        "WHERE status <> 'CLOSED' AND anchor_kind = 'ENTITY' AND anchor_entity_id IS NOT NULL"
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_cases_live_batch ON cases (template_id, anchor_batch_id) "
        "WHERE status <> 'CLOSED' AND anchor_kind = 'BATCH' AND anchor_batch_id IS NOT NULL"
    )
    op.execute("CREATE INDEX ix_cases_board ON cases (workspace_id, status, created_at DESC)")
    op.execute(
        f"""
        CREATE TABLE case_documents (
            id uuid PRIMARY KEY,
            case_id uuid NOT NULL,
            workspace_id uuid NOT NULL,
            work_item_id uuid NOT NULL,
            document_type varchar(64) NOT NULL,
            source varchar(8) NOT NULL,
            added_by_user_id uuid NULL REFERENCES users (id) ON DELETE SET NULL,
            added_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT fk_case_documents_case FOREIGN KEY (case_id, workspace_id)
                REFERENCES cases (id, workspace_id) ON DELETE CASCADE,
            CONSTRAINT fk_case_documents_work_item FOREIGN KEY (work_item_id, workspace_id)
                REFERENCES work_items (id, workspace_id) ON DELETE CASCADE,
            CONSTRAINT uq_case_documents_case_item UNIQUE (case_id, work_item_id),
            CONSTRAINT ck_case_documents_source CHECK (source IN ({_in(DOCUMENT_SOURCES)}))
        )
        """
    )
    op.execute("CREATE INDEX ix_case_documents_work_item ON case_documents (work_item_id)")
    op.execute(
        f"""
        CREATE TABLE case_rule_results (
            id uuid PRIMARY KEY,
            case_id uuid NOT NULL,
            workspace_id uuid NOT NULL,
            rule_id varchar(64) NOT NULL,
            op varchar(12) NOT NULL,
            outcome varchar(8) NOT NULL,
            left_value text NULL,
            right_value text NULL,
            detail jsonb NOT NULL DEFAULT '{{}}'::jsonb,
            evaluated_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT fk_case_rule_results_case FOREIGN KEY (case_id, workspace_id)
                REFERENCES cases (id, workspace_id) ON DELETE CASCADE,
            CONSTRAINT uq_case_rule_results_rule UNIQUE (case_id, rule_id),
            CONSTRAINT ck_case_rule_results_outcome CHECK (outcome IN ({_in(RULE_OUTCOMES)})),
            CONSTRAINT ck_case_rule_results_op CHECK (op IN ('EQUAL', 'FUZZY_EQUAL', 'DATE_ORDER', 'WITHIN_DAYS', 'SUM_EQUALS'))
        )
        """
    )
    op.execute(
        f"""
        CREATE TABLE document_requests (
            id uuid PRIMARY KEY,
            organization_id uuid NOT NULL,
            workspace_id uuid NOT NULL,
            case_id uuid NOT NULL,
            document_type varchar(64) NOT NULL,
            recipient_label varchar(200) NOT NULL DEFAULT '',
            token_hash char(64) NOT NULL,
            status varchar(10) NOT NULL DEFAULT 'OPEN',
            expires_at timestamptz NOT NULL,
            used_at timestamptz NULL,
            fulfilled_work_item_id uuid NULL,
            created_by_user_id uuid NULL REFERENCES users (id) ON DELETE SET NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT uq_document_requests_token UNIQUE (token_hash),
            CONSTRAINT fk_document_requests_case FOREIGN KEY (case_id, workspace_id)
                REFERENCES cases (id, workspace_id) ON DELETE CASCADE,
            CONSTRAINT fk_document_requests_workspace FOREIGN KEY (workspace_id, organization_id)
                REFERENCES workspaces (id, organization_id) ON DELETE CASCADE,
            CONSTRAINT fk_document_requests_work_item FOREIGN KEY (fulfilled_work_item_id, workspace_id)
                REFERENCES work_items (id, workspace_id) ON DELETE SET NULL (fulfilled_work_item_id),
            CONSTRAINT ck_document_requests_status CHECK (status IN ({_in(REQUEST_STATUSES)})),
            CONSTRAINT ck_document_requests_token_hash CHECK (token_hash ~ '^[0-9a-f]{{64}}$'),
            CONSTRAINT ck_document_requests_single_use CHECK ((status = 'FULFILLED') = (used_at IS NOT NULL)),
            CONSTRAINT ck_document_requests_expiry CHECK (expires_at > created_at)
        )
        """
    )
    op.execute("CREATE INDEX ix_document_requests_case ON document_requests (case_id, status)")
    op.execute("CREATE INDEX ix_document_requests_open_expiry ON document_requests (expires_at) WHERE status = 'OPEN'")
    _visibility_check(internal_after_43())

    # -- the review hub learns SPLIT ---------------------------------------------
    op.execute("ALTER TABLE review_assignments DROP CONSTRAINT ck_review_assignments_kind_known")
    op.execute(
        "ALTER TABLE review_assignments ADD CONSTRAINT ck_review_assignments_kind_known "
        f"CHECK (kind IN ({_in(REVIEW_KINDS)}))"
    )
    op.execute("DROP VIEW IF EXISTS review_queue_items")
    op.execute(review_queue_view_v4())


def _visibility_check(values: tuple[str, ...]) -> None:
    """ARCH-37's constraint, same shape byte for byte (arch40_step0._visibility_check)."""
    _step40_0()._visibility_check(values)


def downgrade() -> None:
    op.execute("DELETE FROM outbox_events WHERE event_type IN ('trigger.packet.split', 'trigger.case.completed', "
               "'trigger.case.inconsistent')")
    _visibility_check(tuple(_step40_0().INTERNAL_AFTER_40))
    op.execute("DROP VIEW IF EXISTS review_queue_items")
    op.execute(_step42().review_queue_view_v3())
    op.execute("DELETE FROM review_assignments WHERE kind = 'SPLIT'")
    op.execute("ALTER TABLE review_assignments DROP CONSTRAINT ck_review_assignments_kind_known")
    op.execute(
        "ALTER TABLE review_assignments ADD CONSTRAINT ck_review_assignments_kind_known "
        f"CHECK (kind IN ({_in(REVIEW_KINDS_BEFORE)}))"
    )
    op.execute("DROP INDEX IF EXISTS ix_entity_mentions_live")
    op.execute("ALTER TABLE entity_mentions DROP CONSTRAINT IF EXISTS ck_entity_mentions_superseded")
    op.execute("ALTER TABLE entity_mentions DROP CONSTRAINT IF EXISTS fk_entity_mentions_superseded_by")
    op.execute("ALTER TABLE entity_mentions DROP COLUMN IF EXISTS superseded_by_split_id, DROP COLUMN IF EXISTS superseded_at")
    op.execute("DROP TRIGGER IF EXISTS trg_packet_split_segments_within_packet ON packet_split_segments")
    op.execute("DROP FUNCTION IF EXISTS packet_split_segment_within_packet()")
    op.execute("DROP TRIGGER IF EXISTS trg_case_templates_immutable ON case_templates")
    op.execute("DROP FUNCTION IF EXISTS case_templates_immutable()")
    for table in TABLES_IN_DROP_ORDER:
        op.execute(f"DROP TABLE IF EXISTS {table}")
    op.execute("DROP INDEX IF EXISTS ix_work_items_parent")
    for name in ("ck_work_items_not_own_parent", "ck_work_items_parent_pages", "fk_work_items_parent"):
        op.execute(f"ALTER TABLE work_items DROP CONSTRAINT IF EXISTS {name}")
    op.execute(
        "ALTER TABLE work_items DROP COLUMN IF EXISTS parent_page_end, DROP COLUMN IF EXISTS parent_page_start, "
        "DROP COLUMN IF EXISTS parent_work_item_id"
    )
    op.execute("DROP INDEX IF EXISTS ix_jobs_inflight_by_org")
    op.execute("DROP INDEX IF EXISTS ix_jobs_claimable_by_org")
    # btree_gist is left installed: other schemas may use it and it holds no data.

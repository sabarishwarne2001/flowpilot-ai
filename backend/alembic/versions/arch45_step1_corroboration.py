"""ARCH-45 Step 1 — EXPAND: the Universal Document Corroborator & Discrepancy Matrix.

Revision ID: arch45_step1_corroboration
Revises: arch44_step1_table_intelligence

ARCH45-S1:migration. Expand only. The whole of ARCH-45 in one revision:
nothing existing is dropped or narrowed.

CHAIN POSITION
==============

Inserted between arch44_step1_table_intelligence (the ARCH-44 release head)
and arch40_step3_contract_ai_settings (the flag-gated, lossy contract step),
as hm1 and ARCH-41 to 44 were: the contract step now revises this revision, so
there is still ONE file head and `alembic upgrade head` still refuses the
contract without ARCH40_CONTRACT. run_arch45.ps1 upgrades to this revision by
name and handles a database whose contract already ran.

WHAT IT ADDS
============

  corroboration_runs       one comparison of 2 to 5 documents: status, the
                           document-set hash, the FINGERPRINT (engine version,
                           encoder, options, rules and every document's content
                           hash -- the cache key), counts, per-layer stats and
                           the viewer's alignment anchors. A partial UNIQUE index
                           makes a fingerprint a single live cache entry.
  corroboration_documents  the documents of a run, in the order the user chose
                           (position) with each document's content hash. A
                           composite FK onto work_items (id, workspace_id); when
                           a document is deleted a trigger deletes its runs: a
                           comparison missing a document is no comparison, and
                           its evidence quotes the document.
  corroboration_pairs      per document pair (canonical: left < right) the
                           agreement summary and the clause alignment.
  discrepancies            one row per difference: layer, kind, materiality,
                           severity (CHECK-bound to materiality), per-document
                           values, EVIDENCE SPANS (page + box + text; a trigger
                           refuses a span on a document outside the run) and the
                           reviewer's decision.
  outbox vocabulary        trigger.corroboration.discrepancies becomes a legal
                           INTERNAL event (the ARCH-37 CHECK is rebuilt, same shape).
  review hub               a seventh kind, CORROBORATION (reason
                           MATERIAL_DISCREPANCY): the CHECK is recreated and the
                           view is ARCH-44's text plus one arm (loaded, not copied).
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

from alembic import op

revision = "arch45_step1_corroboration"
down_revision = "arch44_step1_table_intelligence"
branch_labels = None
depends_on = None

#: ARCH45-S1:vocabulary. Mirrors app/services/corroboration/vocabulary.py (verify_arch45 T2).
STATUSES = ("QUEUED", "RUNNING", "COMPLETED", "STALE", "FAILED")
LIVE_STATUSES = ("QUEUED", "RUNNING", "COMPLETED")
LAYERS = ("FIELD", "ENTITY", "CLAUSE", "TABLE", "RULE")
KINDS = ("FIELD_MISMATCH", "FIELD_MISSING", "ENTITY_MISMATCH", "ENTITY_MISSING", "CLAUSE_MODIFIED", "CLAUSE_MISSING",
         "LINE_MISMATCH", "LINE_MISSING", "RULE_CONFLICT", "RULE_VALUE", "RULE_FAILED")
KINDS_BY_LAYER = {"FIELD": ("FIELD_MISMATCH", "FIELD_MISSING"), "ENTITY": ("ENTITY_MISMATCH", "ENTITY_MISSING"),
                  "CLAUSE": ("CLAUSE_MODIFIED", "CLAUSE_MISSING"), "TABLE": ("LINE_MISMATCH", "LINE_MISSING"),
                  "RULE": ("RULE_CONFLICT", "RULE_VALUE", "RULE_FAILED")}
SEVERITIES = ("HIGH", "MEDIUM", "LOW")
DECISIONS = ("OPEN", "CONFIRMED", "DISMISSED")
ENCODERS = ("lexical-v1", "st-all-minilm-l6-v2")

#: ARCH45-S1:review-kinds. ARCH-44's six + CORROBORATION.
REVIEW_KINDS = ("EXTRACTION", "ASSERTION", "ANOMALY", "MERGE", "SPLIT", "TABLE", "CORROBORATION")
REVIEW_KINDS_BEFORE = ("EXTRACTION", "ASSERTION", "ANOMALY", "MERGE", "SPLIT", "TABLE")
#: ARCH45-S1:review-reasons. ARCH-44's ten + MATERIAL_DISCREPANCY.
REVIEW_REASONS = (
    "DISAGREEMENT", "ESCALATION", "CALIBRATION_HOLD", "AUTONOMY_AUDIT",
    "PENDING_REVIEW", "CLAUSE_TRIAGE", "ANOMALY", "ENTITY_MERGE", "PACKET_SPLIT", "TABLE_ARITHMETIC",
    "MATERIAL_DISCREPANCY",
)
#: ARCH45-S1:trigger-events. The one new INTERNAL trigger event.
NEW_TRIGGER_EVENTS = ("trigger.corroboration.discrepancies",)

TABLES_IN_DROP_ORDER = ("discrepancies", "corroboration_pairs", "corroboration_documents", "corroboration_runs")


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(f"{name}.py"))
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _step44():
    return _load("arch44_step1_table_intelligence")


def internal_after_45() -> tuple[str, ...]:
    return tuple(_step44().internal_after_44()) + NEW_TRIGGER_EVENTS


def _in(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{x}'" for x in values)


#: ARCH45-S1:review-view-v6. ARCH-44's view, unchanged, plus a CORROBORATION
#: arm. A completed run with open MATERIAL discrepancies is OPEN; deciding
#: every one of them (per discrepancy, or all at once from the hub) resolves it.
#: A run that went STALE leaves the hub unless someone already reviewed it.
CORROBORATION_ARM = """
    UNION ALL

    SELECT
        'CORROBORATION'::varchar(16),
        r.id,
        r.organization_id,
        r.workspace_id,
        d.work_item_id,
        ('Documents disagree: ' || wi.original_filename || ' and ' || (r.document_count - 1) || ' other(s), '
            || r.material_count || ' material difference(s)')::text,
        CASE WHEN r.max_materiality >= 0.75 THEN 'HIGH' ELSE 'MEDIUM' END::varchar(8),
        CASE WHEN r.max_materiality >= 0.75 THEN 2 ELSE 3 END::integer,
        (1 - r.max_materiality)::numeric(5, 4),
        COALESCE(r.completed_at, r.created_at),
        CASE WHEN r.status = 'COMPLETED' AND r.open_material_count > 0 THEN 'OPEN' ELSE 'RESOLVED' END::varchar(8),
        r.reviewed_at,
        r.reviewed_by_user_id,
        'MATERIAL_DISCREPANCY'::varchar(24)
    FROM corroboration_runs r
    JOIN corroboration_documents d ON d.run_id = r.id AND d.position = 0
    JOIN work_items wi ON wi.id = d.work_item_id
    WHERE (r.status = 'COMPLETED' AND r.material_count > 0) OR r.reviewed_at IS NOT NULL
"""


def review_queue_view_v6() -> str:
    return _step44().review_queue_view_v5().rstrip() + "\n" + CORROBORATION_ARM


def _layer_kind_check() -> str:
    arms = [f"(layer = '{layer}' AND kind IN ({_in(kinds)}))" for layer, kinds in KINDS_BY_LAYER.items()]
    return " OR ".join(arms)


def upgrade() -> None:
    op.execute(
        f"""
        CREATE TABLE corroboration_runs (
            id uuid PRIMARY KEY,
            organization_id uuid NOT NULL,
            workspace_id uuid NOT NULL,
            created_by_user_id uuid NULL REFERENCES users (id) ON DELETE SET NULL,
            status varchar(10) NOT NULL,
            set_hash char(64) NOT NULL,
            fingerprint char(64) NOT NULL,
            engine_version varchar(24) NOT NULL,
            encoder varchar(32) NOT NULL,
            options jsonb NOT NULL DEFAULT '{{}}'::jsonb,
            rules jsonb NOT NULL DEFAULT '[]'::jsonb,
            layers jsonb NOT NULL DEFAULT '{{}}'::jsonb,
            stats jsonb NOT NULL DEFAULT '{{}}'::jsonb,
            anchors jsonb NOT NULL DEFAULT '[]'::jsonb,
            document_count smallint NOT NULL,
            discrepancy_count integer NOT NULL DEFAULT 0,
            material_count integer NOT NULL DEFAULT 0,
            open_material_count integer NOT NULL DEFAULT 0,
            max_materiality numeric(5, 4) NOT NULL DEFAULT 0,
            error text NULL,
            revision integer NOT NULL DEFAULT 1,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            started_at timestamptz NULL,
            completed_at timestamptz NULL,
            stale_at timestamptz NULL,
            reviewed_at timestamptz NULL,
            reviewed_by_user_id uuid NULL REFERENCES users (id) ON DELETE SET NULL,
            CONSTRAINT uq_corroboration_runs_id_workspace UNIQUE (id, workspace_id),
            CONSTRAINT fk_corroboration_runs_workspace FOREIGN KEY (workspace_id, organization_id)
                REFERENCES workspaces (id, organization_id) ON DELETE CASCADE,
            CONSTRAINT ck_corroboration_runs_status CHECK (status IN ({_in(STATUSES)})),
            CONSTRAINT ck_corroboration_runs_encoder CHECK (encoder IN ({_in(ENCODERS)})),
            CONSTRAINT ck_corroboration_runs_documents CHECK (document_count BETWEEN 2 AND 5),
            CONSTRAINT ck_corroboration_runs_hashes CHECK (set_hash ~ '^[0-9a-f]{{64}}$' AND fingerprint ~ '^[0-9a-f]{{64}}$'),
            CONSTRAINT ck_corroboration_runs_counts CHECK (discrepancy_count >= 0 AND material_count >= 0
                AND open_material_count >= 0 AND open_material_count <= material_count
                AND material_count <= discrepancy_count AND revision >= 1),
            CONSTRAINT ck_corroboration_runs_materiality CHECK (max_materiality >= 0 AND max_materiality <= 1),
            CONSTRAINT ck_corroboration_runs_completed CHECK ((status IN ('COMPLETED', 'STALE')) = (completed_at IS NOT NULL)),
            CONSTRAINT ck_corroboration_runs_failed CHECK ((status = 'FAILED') = (error IS NOT NULL)),
            CONSTRAINT ck_corroboration_runs_stale CHECK (status <> 'STALE' OR stale_at IS NOT NULL),
            CONSTRAINT ck_corroboration_runs_reviewed CHECK (reviewed_by_user_id IS NULL OR reviewed_at IS NOT NULL),
            CONSTRAINT ck_corroboration_runs_json CHECK (jsonb_typeof(options) = 'object' AND jsonb_typeof(rules) = 'array'
                AND jsonb_typeof(layers) = 'object' AND jsonb_typeof(stats) = 'object'
                AND jsonb_typeof(anchors) = 'array')
        )
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_corroboration_runs_live ON corroboration_runs (workspace_id, fingerprint) "
        f"WHERE status IN ({_in(LIVE_STATUSES)})"
    )
    op.execute("CREATE INDEX ix_corroboration_runs_workspace ON corroboration_runs (workspace_id, created_at DESC)")
    op.execute("CREATE INDEX ix_corroboration_runs_set ON corroboration_runs (workspace_id, set_hash)")
    op.execute(
        """
        CREATE TABLE corroboration_documents (
            run_id uuid NOT NULL,
            workspace_id uuid NOT NULL,
            work_item_id uuid NOT NULL,
            position smallint NOT NULL,
            label varchar(255) NOT NULL,
            content_hash char(64) NOT NULL,
            page_count integer NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT pk_corroboration_documents PRIMARY KEY (run_id, work_item_id),
            CONSTRAINT uq_corroboration_documents_position UNIQUE (run_id, position),
            CONSTRAINT fk_corroboration_documents_run FOREIGN KEY (run_id, workspace_id)
                REFERENCES corroboration_runs (id, workspace_id) ON DELETE CASCADE,
            CONSTRAINT fk_corroboration_documents_work_item FOREIGN KEY (work_item_id, workspace_id)
                REFERENCES work_items (id, workspace_id) ON DELETE CASCADE,
            CONSTRAINT ck_corroboration_documents_position CHECK (position BETWEEN 0 AND 4),
            CONSTRAINT ck_corroboration_documents_hash CHECK (content_hash ~ '^[0-9a-f]{64}$'),
            CONSTRAINT ck_corroboration_documents_pages CHECK (page_count IS NULL OR page_count >= 0)
        )
        """
    )
    op.execute("CREATE INDEX ix_corroboration_documents_work_item ON corroboration_documents (work_item_id)")
    op.execute(
        """
        CREATE FUNCTION corroboration_document_position_valid() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE n smallint;
        BEGIN
            SELECT document_count INTO n FROM corroboration_runs WHERE id = NEW.run_id;
            IF FOUND AND NEW.position >= n THEN
                RAISE EXCEPTION 'document position % is outside the run''s % documents', NEW.position, n
                    USING ERRCODE = '23514', CONSTRAINT = 'ck_corroboration_documents_within_run';
            END IF;
            RETURN NEW;
        END $$
        """
    )
    op.execute(
        "CREATE TRIGGER trg_corroboration_documents_position BEFORE INSERT OR UPDATE ON corroboration_documents "
        "FOR EACH ROW EXECUTE FUNCTION corroboration_document_position_valid()"
    )
    op.execute(
        """
        CREATE FUNCTION corroboration_document_removed() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            DELETE FROM corroboration_runs WHERE id = OLD.run_id;
            RETURN OLD;
        END $$
        """
    )
    op.execute(
        "CREATE TRIGGER trg_corroboration_documents_removed AFTER DELETE ON corroboration_documents "
        "FOR EACH ROW EXECUTE FUNCTION corroboration_document_removed()"
    )
    op.execute(
        """
        CREATE TABLE corroboration_pairs (
            id uuid PRIMARY KEY,
            run_id uuid NOT NULL,
            workspace_id uuid NOT NULL,
            left_work_item_id uuid NOT NULL,
            right_work_item_id uuid NOT NULL,
            clauses_left integer NOT NULL DEFAULT 0,
            clauses_right integer NOT NULL DEFAULT 0,
            clauses_matched integer NOT NULL DEFAULT 0,
            clauses_identical integer NOT NULL DEFAULT 0,
            clause_similarity numeric(5, 4) NOT NULL DEFAULT 0,
            fields_compared integer NOT NULL DEFAULT 0,
            fields_agreeing integer NOT NULL DEFAULT 0,
            lines_left integer NOT NULL DEFAULT 0,
            lines_right integer NOT NULL DEFAULT 0,
            lines_matched integer NOT NULL DEFAULT 0,
            lines_agreeing integer NOT NULL DEFAULT 0,
            entities_compared integer NOT NULL DEFAULT 0,
            entities_agreeing integer NOT NULL DEFAULT 0,
            discrepancy_count integer NOT NULL DEFAULT 0,
            material_count integer NOT NULL DEFAULT 0,
            agreement numeric(5, 4) NOT NULL DEFAULT 1,
            alignment jsonb NOT NULL DEFAULT '[]'::jsonb,
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT uq_corroboration_pairs UNIQUE (run_id, left_work_item_id, right_work_item_id),
            CONSTRAINT fk_corroboration_pairs_run FOREIGN KEY (run_id, workspace_id)
                REFERENCES corroboration_runs (id, workspace_id) ON DELETE CASCADE,
            CONSTRAINT fk_corroboration_pairs_left FOREIGN KEY (left_work_item_id, workspace_id)
                REFERENCES work_items (id, workspace_id) ON DELETE CASCADE,
            CONSTRAINT fk_corroboration_pairs_right FOREIGN KEY (right_work_item_id, workspace_id)
                REFERENCES work_items (id, workspace_id) ON DELETE CASCADE,
            CONSTRAINT ck_corroboration_pairs_canonical CHECK (left_work_item_id::text < right_work_item_id::text),
            CONSTRAINT ck_corroboration_pairs_counts CHECK (clauses_left >= 0 AND clauses_right >= 0
                AND clauses_matched >= 0 AND clauses_identical >= 0 AND clauses_identical <= clauses_matched
                AND fields_compared >= 0 AND fields_agreeing >= 0 AND fields_agreeing <= fields_compared
                AND lines_left >= 0 AND lines_right >= 0 AND lines_matched >= 0 AND lines_agreeing >= 0
                AND lines_agreeing <= lines_matched AND entities_compared >= 0 AND entities_agreeing >= 0
                AND entities_agreeing <= entities_compared AND discrepancy_count >= 0 AND material_count >= 0
                AND material_count <= discrepancy_count),
            CONSTRAINT ck_corroboration_pairs_scores CHECK (clause_similarity >= 0 AND clause_similarity <= 1
                AND agreement >= 0 AND agreement <= 1),
            CONSTRAINT ck_corroboration_pairs_json CHECK (jsonb_typeof(alignment) = 'array')
        )
        """
    )
    op.execute("CREATE INDEX ix_corroboration_pairs_run ON corroboration_pairs (run_id)")
    op.execute(
        f"""
        CREATE TABLE discrepancies (
            id uuid PRIMARY KEY,
            run_id uuid NOT NULL,
            workspace_id uuid NOT NULL,
            ordinal integer NOT NULL,
            layer varchar(8) NOT NULL,
            kind varchar(16) NOT NULL,
            group_key varchar(300) NOT NULL,
            label varchar(300) NOT NULL,
            summary text NOT NULL DEFAULT '',
            materiality numeric(5, 4) NOT NULL,
            severity varchar(8) NOT NULL,
            is_material boolean NOT NULL,
            doc_values jsonb NOT NULL DEFAULT '{{}}'::jsonb,
            evidence jsonb NOT NULL DEFAULT '[]'::jsonb,
            detail jsonb NOT NULL DEFAULT '{{}}'::jsonb,
            status varchar(10) NOT NULL DEFAULT 'OPEN',
            decided_at timestamptz NULL,
            decided_by_user_id uuid NULL REFERENCES users (id) ON DELETE SET NULL,
            note text NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT uq_discrepancies_ordinal UNIQUE (run_id, ordinal),
            CONSTRAINT uq_discrepancies_key UNIQUE (run_id, kind, group_key),
            CONSTRAINT fk_discrepancies_run FOREIGN KEY (run_id, workspace_id)
                REFERENCES corroboration_runs (id, workspace_id) ON DELETE CASCADE,
            CONSTRAINT ck_discrepancies_ordinal CHECK (ordinal >= 0),
            CONSTRAINT ck_discrepancies_layer CHECK (layer IN ({_in(LAYERS)})),
            CONSTRAINT ck_discrepancies_kind CHECK (kind IN ({_in(KINDS)})),
            CONSTRAINT ck_discrepancies_layer_kind CHECK ({_layer_kind_check()}),
            CONSTRAINT ck_discrepancies_materiality CHECK (materiality >= 0 AND materiality <= 1),
            CONSTRAINT ck_discrepancies_severity CHECK (
                (severity = 'HIGH' AND materiality >= 0.75)
                OR (severity = 'MEDIUM' AND materiality >= 0.5 AND materiality < 0.75)
                OR (severity = 'LOW' AND materiality < 0.5)),
            CONSTRAINT ck_discrepancies_status CHECK (status IN ({_in(DECISIONS)})),
            CONSTRAINT ck_discrepancies_decided CHECK ((status = 'OPEN') = (decided_at IS NULL)),
            CONSTRAINT ck_discrepancies_note CHECK (note IS NULL OR length(note) <= 2000),
            CONSTRAINT ck_discrepancies_json CHECK (jsonb_typeof(doc_values) = 'object'
                AND jsonb_typeof(evidence) = 'array' AND jsonb_typeof(detail) = 'object')
        )
        """
    )
    op.execute("CREATE INDEX ix_discrepancies_run ON discrepancies (run_id, is_material, status)")
    op.execute(
        """
        CREATE FUNCTION discrepancy_evidence_valid() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE span jsonb;
        BEGIN
            FOR span IN SELECT * FROM jsonb_array_elements(NEW.evidence) LOOP
                IF jsonb_typeof(span) <> 'object' OR NOT (span ? 'work_item_id') OR NOT (span ? 'page')
                   OR jsonb_typeof(span -> 'page') <> 'number' OR (span ->> 'page')::numeric < 1 THEN
                    RAISE EXCEPTION 'an evidence span needs a work_item_id and a page >= 1: %', span
                        USING ERRCODE = '23514', CONSTRAINT = 'ck_discrepancies_evidence_shape';
                END IF;
                IF NOT EXISTS (SELECT 1 FROM corroboration_documents d
                               WHERE d.run_id = NEW.run_id AND d.work_item_id::text = span ->> 'work_item_id') THEN
                    RAISE EXCEPTION 'evidence points at document % which is not in this comparison',
                        span ->> 'work_item_id'
                        USING ERRCODE = '23514', CONSTRAINT = 'ck_discrepancies_evidence_in_run';
                END IF;
            END LOOP;
            RETURN NEW;
        END $$
        """
    )
    op.execute(
        "CREATE TRIGGER trg_discrepancies_evidence BEFORE INSERT OR UPDATE OF evidence ON discrepancies "
        "FOR EACH ROW EXECUTE FUNCTION discrepancy_evidence_valid()"
    )

    _visibility_check(internal_after_45())

    # -- the review hub learns CORROBORATION ------------------------------------
    op.execute("ALTER TABLE review_assignments DROP CONSTRAINT ck_review_assignments_kind_known")
    op.execute(
        "ALTER TABLE review_assignments ADD CONSTRAINT ck_review_assignments_kind_known "
        f"CHECK (kind IN ({_in(REVIEW_KINDS)}))"
    )
    op.execute("DROP VIEW IF EXISTS review_queue_items")
    op.execute(review_queue_view_v6())


def _visibility_check(values: tuple[str, ...]) -> None:
    """ARCH-37's constraint, same shape byte for byte (arch40_step0._visibility_check)."""
    _load("arch40_step0_review_vocabulary")._visibility_check(values)


def downgrade() -> None:
    op.execute("DELETE FROM outbox_events WHERE event_type = 'trigger.corroboration.discrepancies'")
    _visibility_check(tuple(_step44().internal_after_44()))
    op.execute("DROP VIEW IF EXISTS review_queue_items")
    op.execute(_step44().review_queue_view_v5())
    op.execute("DELETE FROM review_assignments WHERE kind = 'CORROBORATION'")
    op.execute("ALTER TABLE review_assignments DROP CONSTRAINT ck_review_assignments_kind_known")
    op.execute(
        "ALTER TABLE review_assignments ADD CONSTRAINT ck_review_assignments_kind_known "
        f"CHECK (kind IN ({_in(REVIEW_KINDS_BEFORE)}))"
    )
    op.execute("DROP TRIGGER IF EXISTS trg_discrepancies_evidence ON discrepancies")
    op.execute("DROP FUNCTION IF EXISTS discrepancy_evidence_valid()")
    op.execute("DROP TRIGGER IF EXISTS trg_corroboration_documents_removed ON corroboration_documents")
    op.execute("DROP TRIGGER IF EXISTS trg_corroboration_documents_position ON corroboration_documents")
    for table in TABLES_IN_DROP_ORDER:
        op.execute(f"DROP TABLE IF EXISTS {table}")
    op.execute("DROP FUNCTION IF EXISTS corroboration_document_removed()")
    op.execute("DROP FUNCTION IF EXISTS corroboration_document_position_valid()")

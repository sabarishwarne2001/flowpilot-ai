"""ARCH-44 Step 1 — EXPAND: the Complex Table & Hierarchical Grid Extractor.

Revision ID: arch44_step1_table_intelligence
Revises: arch43_step1_case_intelligence

ARCH44-S1:migration. Expand only. The whole of ARCH-44 in one revision:
nothing existing is dropped or narrowed.

CHAIN POSITION
==============

Inserted between arch43_step1_case_intelligence (the ARCH-43 release head) and
arch40_step3_contract_ai_settings (the flag-gated, lossy contract step), as
hm1 and ARCH-41, 42 and 43 were: the contract step now revises this revision,
so there is still ONE file head and `alembic upgrade head` still refuses the
contract without ARCH40_CONTRACT. run_arch44.ps1 upgrades to this revision by
name and handles a database whose contract already ran.

WHAT IT ADDS
============

  extracted_tables         one row per logical table (a statement that runs
                           over three pages is ONE table): page range, grid
                           size, header depth, method (STREAM / LATTICE /
                           HYBRID), rotation and skew corrected, column
                           descriptors (label path, type, role), row
                           descriptors (kind, level, page, parent), status.
                           Composite FK onto work_items (id, workspace_id).
  extracted_table_cells    one row per non-empty cell, keyed (table, row, col):
                           spans, typed value (numeric(24,6) or date, never
                           both, never on an EMPTY cell), OCR / base / final
                           confidence each in [0, 1], flags. A trigger refuses
                           a cell outside its table's grid or page range.
  table_validations        arithmetic relations (running balance, row product,
                           row total, column sum, hierarchy sum, carry forward)
                           and the cells that failed them.
  table_column_mappings    column roles learned per table layout from reviewer
                           corrections (Wilson-bounded, ARCH-41's z), linked to
                           the ARCH-41 layout template when there is one.
  outbox vocabulary        trigger.table.flagged becomes a legal INTERNAL event
                           (the ARCH-37 CHECK is rebuilt, same shape).
  review hub               a sixth kind, TABLE (reason TABLE_ARITHMETIC): the
                           CHECK is recreated and the view is ARCH-43's text
                           plus one arm (loaded, not copied).
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

from alembic import op

revision = "arch44_step1_table_intelligence"
down_revision = "arch43_step1_case_intelligence"
branch_labels = None
depends_on = None

#: ARCH44-S1:vocabulary. Mirrors app/services/tables/vocabulary.py (verify_arch44 T2).
STATUSES = ("EXTRACTED", "VALIDATED", "FLAGGED", "REVIEWED", "REJECTED")
METHODS = ("STREAM", "LATTICE", "HYBRID")
VALUE_TYPES = ("TEXT", "NUMBER", "MONEY", "DATE", "PERCENT", "EMPTY")
FLAGS = ("ARITH_FAIL", "TYPE_MISMATCH", "LOW_OCR", "SPILL", "CORRECTED")
CHECK_KINDS = ("RUNNING_BALANCE", "ROW_PRODUCT", "ROW_TOTAL", "COLUMN_SUM", "HIERARCHY_SUM", "CARRY_FORWARD")
ROLES = ("DATE", "VALUE_DATE", "DESCRIPTION", "REFERENCE", "DEBIT", "CREDIT", "AMOUNT", "BALANCE", "QUANTITY",
         "UNIT_PRICE", "TAX", "DISCOUNT", "TOTAL", "CODE", "UNIT", "RANGE", "PERCENT", "OTHER")

#: ARCH44-S1:review-kinds. ARCH-43's five + TABLE.
REVIEW_KINDS = ("EXTRACTION", "ASSERTION", "ANOMALY", "MERGE", "SPLIT", "TABLE")
REVIEW_KINDS_BEFORE = ("EXTRACTION", "ASSERTION", "ANOMALY", "MERGE", "SPLIT")
#: ARCH44-S1:review-reasons. ARCH-43's nine + TABLE_ARITHMETIC.
REVIEW_REASONS = (
    "DISAGREEMENT", "ESCALATION", "CALIBRATION_HOLD", "AUTONOMY_AUDIT",
    "PENDING_REVIEW", "CLAUSE_TRIAGE", "ANOMALY", "ENTITY_MERGE", "PACKET_SPLIT", "TABLE_ARITHMETIC",
)
#: ARCH44-S1:trigger-events. The one new INTERNAL trigger event.
NEW_TRIGGER_EVENTS = ("trigger.table.flagged",)

TABLES_IN_DROP_ORDER = ("table_column_mappings", "table_validations", "extracted_table_cells", "extracted_tables")


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(f"{name}.py"))
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _step43():
    return _load("arch43_step1_case_intelligence")


def internal_after_44() -> tuple[str, ...]:
    return tuple(_step43().internal_after_43()) + NEW_TRIGGER_EVENTS


def _in(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{x}'" for x in values)


#: ARCH44-S1:review-view-v5. ARCH-43's view, unchanged, plus a TABLE arm. A
#: table is OPEN while FLAGGED; a reviewer's decision, or a correction that
#: makes every figure reconcile, resolves it.
TABLE_ARM = """
    UNION ALL

    SELECT
        'TABLE'::varchar(16),
        t.id,
        t.organization_id,
        t.workspace_id,
        t.work_item_id,
        ('Table ' || (t.ordinal + 1) || ' in ' || wi.original_filename || ': '
            || t.failed_checks || ' figure(s) do not reconcile')::text,
        CASE WHEN t.failed_checks >= 3 THEN 'HIGH' ELSE 'MEDIUM' END::varchar(8),
        CASE WHEN t.failed_checks >= 3 THEN 2 ELSE 3 END::integer,
        t.confidence,
        t.created_at,
        CASE WHEN t.status = 'FLAGGED' THEN 'OPEN' ELSE 'RESOLVED' END::varchar(8),
        COALESCE(t.reviewed_at, t.corrected_at),
        COALESCE(t.reviewed_by_user_id, t.corrected_by_user_id),
        'TABLE_ARITHMETIC'::varchar(24)
    FROM extracted_tables t
    JOIN work_items wi ON wi.id = t.work_item_id
    WHERE t.status = 'FLAGGED' OR t.reviewed_at IS NOT NULL OR t.corrected_at IS NOT NULL
"""


def review_queue_view_v5() -> str:
    return _step43().review_queue_view_v4().rstrip() + "\n" + TABLE_ARM


def upgrade() -> None:
    op.execute(
        f"""
        CREATE TABLE extracted_tables (
            id uuid PRIMARY KEY,
            organization_id uuid NOT NULL,
            workspace_id uuid NOT NULL,
            work_item_id uuid NOT NULL,
            ordinal integer NOT NULL,
            page_start integer NOT NULL,
            page_end integer NOT NULL,
            n_rows integer NOT NULL,
            n_cols integer NOT NULL,
            header_rows integer NOT NULL DEFAULT 0,
            title varchar(300) NULL,
            method varchar(8) NOT NULL,
            rotation smallint NOT NULL DEFAULT 0,
            skew_degrees numeric(5, 2) NOT NULL DEFAULT 0,
            columns jsonb NOT NULL DEFAULT '[]'::jsonb,
            rows jsonb NOT NULL DEFAULT '[]'::jsonb,
            bboxes jsonb NOT NULL DEFAULT '[]'::jsonb,
            confidence numeric(5, 4) NOT NULL,
            status varchar(10) NOT NULL,
            checked_relations integer NOT NULL DEFAULT 0,
            failed_checks integer NOT NULL DEFAULT 0,
            layout_key char(40) NOT NULL,
            engine_version varchar(24) NOT NULL,
            revision integer NOT NULL DEFAULT 1,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            corrected_at timestamptz NULL,
            corrected_by_user_id uuid NULL REFERENCES users (id) ON DELETE SET NULL,
            reviewed_at timestamptz NULL,
            reviewed_by_user_id uuid NULL REFERENCES users (id) ON DELETE SET NULL,
            CONSTRAINT uq_extracted_tables_id_workspace UNIQUE (id, workspace_id),
            CONSTRAINT uq_extracted_tables_ordinal UNIQUE (work_item_id, ordinal),
            CONSTRAINT fk_extracted_tables_workspace FOREIGN KEY (workspace_id, organization_id)
                REFERENCES workspaces (id, organization_id) ON DELETE CASCADE,
            CONSTRAINT fk_extracted_tables_work_item FOREIGN KEY (work_item_id, workspace_id)
                REFERENCES work_items (id, workspace_id) ON DELETE CASCADE,
            CONSTRAINT ck_extracted_tables_pages CHECK (page_start >= 1 AND page_end >= page_start),
            CONSTRAINT ck_extracted_tables_dims CHECK (n_rows >= 1 AND n_cols >= 1 AND n_rows <= 200000 AND n_cols <= 200),
            CONSTRAINT ck_extracted_tables_header CHECK (header_rows >= 0 AND header_rows < n_rows),
            CONSTRAINT ck_extracted_tables_ordinal CHECK (ordinal >= 0),
            CONSTRAINT ck_extracted_tables_method CHECK (method IN ({_in(METHODS)})),
            CONSTRAINT ck_extracted_tables_rotation CHECK (rotation IN (0, 90, 180, 270)),
            CONSTRAINT ck_extracted_tables_confidence CHECK (confidence >= 0 AND confidence <= 1),
            CONSTRAINT ck_extracted_tables_status CHECK (status IN ({_in(STATUSES)})),
            CONSTRAINT ck_extracted_tables_counts CHECK (checked_relations >= 0 AND failed_checks >= 0 AND revision >= 1),
            CONSTRAINT ck_extracted_tables_flagged CHECK (status <> 'FLAGGED' OR failed_checks > 0),
            CONSTRAINT ck_extracted_tables_reviewed CHECK ((status IN ('REVIEWED', 'REJECTED')) = (reviewed_at IS NOT NULL)),
            CONSTRAINT ck_extracted_tables_layout CHECK (layout_key ~ '^[0-9a-f]{{40}}$'),
            CONSTRAINT ck_extracted_tables_json CHECK (jsonb_typeof(columns) = 'array' AND jsonb_typeof(rows) = 'array'
                AND jsonb_typeof(bboxes) = 'array'),
            CONSTRAINT ck_extracted_tables_rows_len CHECK (jsonb_array_length(rows) = n_rows),
            CONSTRAINT ck_extracted_tables_cols_len CHECK (jsonb_array_length(columns) = n_cols)
        )
        """
    )
    op.execute("CREATE INDEX ix_extracted_tables_workspace ON extracted_tables (workspace_id, status, created_at DESC)")
    op.execute("CREATE INDEX ix_extracted_tables_work_item ON extracted_tables (work_item_id)")
    op.execute(
        f"""
        CREATE TABLE extracted_table_cells (
            table_id uuid NOT NULL,
            workspace_id uuid NOT NULL,
            row_index integer NOT NULL,
            col_index integer NOT NULL,
            row_span integer NOT NULL DEFAULT 1,
            col_span integer NOT NULL DEFAULT 1,
            page integer NOT NULL,
            text text NOT NULL DEFAULT '',
            value_type varchar(8) NOT NULL,
            value_number numeric(24, 6) NULL,
            value_date date NULL,
            ocr_confidence numeric(5, 4) NOT NULL DEFAULT 1,
            base_confidence numeric(5, 4) NOT NULL,
            confidence numeric(5, 4) NOT NULL,
            flags varchar(16)[] NOT NULL DEFAULT '{{}}',
            bbox jsonb NULL,
            original_text text NULL,
            corrected_at timestamptz NULL,
            corrected_by_user_id uuid NULL REFERENCES users (id) ON DELETE SET NULL,
            CONSTRAINT pk_extracted_table_cells PRIMARY KEY (table_id, row_index, col_index),
            CONSTRAINT fk_extracted_table_cells_table FOREIGN KEY (table_id, workspace_id)
                REFERENCES extracted_tables (id, workspace_id) ON DELETE CASCADE,
            CONSTRAINT ck_extracted_table_cells_row CHECK (row_index >= 0),
            CONSTRAINT ck_extracted_table_cells_col CHECK (col_index >= 0),
            CONSTRAINT ck_extracted_table_cells_span CHECK (row_span >= 1 AND col_span >= 1),
            CONSTRAINT ck_extracted_table_cells_page CHECK (page >= 1),
            CONSTRAINT ck_extracted_table_cells_confidence CHECK (
                confidence >= 0 AND confidence <= 1 AND base_confidence >= 0 AND base_confidence <= 1
                AND ocr_confidence >= 0 AND ocr_confidence <= 1),
            CONSTRAINT ck_extracted_table_cells_type CHECK (value_type IN ({_in(VALUE_TYPES)})),
            CONSTRAINT ck_extracted_table_cells_number CHECK (
                (value_number IS NOT NULL) = (value_type IN ('NUMBER', 'MONEY', 'PERCENT'))),
            CONSTRAINT ck_extracted_table_cells_date CHECK ((value_date IS NOT NULL) = (value_type = 'DATE')),
            CONSTRAINT ck_extracted_table_cells_empty CHECK ((value_type = 'EMPTY') = (text = '')),
            CONSTRAINT ck_extracted_table_cells_flags CHECK (flags <@ ARRAY[{_in(FLAGS)}]::varchar(16)[]),
            CONSTRAINT ck_extracted_table_cells_corrected CHECK ((corrected_at IS NULL) = (original_text IS NULL))
        )
        """
    )
    op.execute("CREATE INDEX ix_extracted_table_cells_flagged ON extracted_table_cells (table_id) WHERE flags <> '{}'")
    op.execute(
        """
        CREATE FUNCTION extracted_table_cell_within_grid() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE t record;
        BEGIN
            SELECT n_rows, n_cols, page_start, page_end INTO t FROM extracted_tables WHERE id = NEW.table_id;
            IF FOUND AND (NEW.row_index + NEW.row_span > t.n_rows OR NEW.col_index + NEW.col_span > t.n_cols) THEN
                RAISE EXCEPTION 'cell (%, %) spanning (%, %) lies outside the table''s % x % grid',
                    NEW.row_index, NEW.col_index, NEW.row_span, NEW.col_span, t.n_rows, t.n_cols
                    USING ERRCODE = '23514', CONSTRAINT = 'ck_extracted_table_cells_within_grid';
            END IF;
            IF FOUND AND (NEW.page < t.page_start OR NEW.page > t.page_end) THEN
                RAISE EXCEPTION 'cell page % lies outside the table''s pages %-%', NEW.page, t.page_start, t.page_end
                    USING ERRCODE = '23514', CONSTRAINT = 'ck_extracted_table_cells_page_range';
            END IF;
            RETURN NEW;
        END $$
        """
    )
    op.execute(
        "CREATE TRIGGER trg_extracted_table_cells_within_grid BEFORE INSERT OR UPDATE ON extracted_table_cells "
        "FOR EACH ROW EXECUTE FUNCTION extracted_table_cell_within_grid()"
    )
    op.execute(
        f"""
        CREATE TABLE table_validations (
            id uuid PRIMARY KEY,
            table_id uuid NOT NULL,
            workspace_id uuid NOT NULL,
            kind varchar(16) NOT NULL,
            scope varchar(8) NOT NULL,
            outcome varchar(4) NOT NULL,
            row_index integer NULL,
            col_index integer NULL,
            expected numeric(24, 6) NULL,
            actual numeric(24, 6) NULL,
            checked integer NOT NULL DEFAULT 0,
            failed integer NOT NULL DEFAULT 0,
            message text NOT NULL DEFAULT '',
            detail jsonb NOT NULL DEFAULT '{{}}'::jsonb,
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT fk_table_validations_table FOREIGN KEY (table_id, workspace_id)
                REFERENCES extracted_tables (id, workspace_id) ON DELETE CASCADE,
            CONSTRAINT ck_table_validations_kind CHECK (kind IN ({_in(CHECK_KINDS)})),
            CONSTRAINT ck_table_validations_scope CHECK (scope IN ('RELATION', 'CELL')),
            CONSTRAINT ck_table_validations_outcome CHECK (outcome IN ('PASS', 'FAIL')),
            CONSTRAINT ck_table_validations_cell CHECK (scope <> 'CELL' OR (row_index IS NOT NULL AND col_index IS NOT NULL
                AND row_index >= 0 AND col_index >= 0 AND outcome = 'FAIL')),
            CONSTRAINT ck_table_validations_relation CHECK (scope <> 'RELATION' OR (checked >= 1 AND failed >= 0
                AND failed <= checked AND (outcome = 'PASS') = (failed = 0))),
            CONSTRAINT ck_table_validations_detail CHECK (jsonb_typeof(detail) = 'object')
        )
        """
    )
    op.execute("CREATE INDEX ix_table_validations_table ON table_validations (table_id, scope, outcome)")
    op.execute(
        f"""
        CREATE TABLE table_column_mappings (
            id uuid PRIMARY KEY,
            organization_id uuid NOT NULL,
            workspace_id uuid NOT NULL,
            layout_key char(40) NOT NULL,
            header_key varchar(200) NOT NULL,
            role varchar(16) NOT NULL,
            confirmations integer NOT NULL DEFAULT 0,
            contradictions integer NOT NULL DEFAULT 0,
            template_id uuid NULL,
            updated_by_user_id uuid NULL REFERENCES users (id) ON DELETE SET NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT uq_table_column_mappings UNIQUE (workspace_id, layout_key, header_key, role),
            CONSTRAINT fk_table_column_mappings_workspace FOREIGN KEY (workspace_id, organization_id)
                REFERENCES workspaces (id, organization_id) ON DELETE CASCADE,
            CONSTRAINT fk_table_column_mappings_template FOREIGN KEY (template_id, workspace_id)
                REFERENCES extraction_templates (id, workspace_id) ON DELETE SET NULL (template_id),
            CONSTRAINT ck_table_column_mappings_role CHECK (role IN ({_in(ROLES)})),
            CONSTRAINT ck_table_column_mappings_counts CHECK (confirmations >= 0 AND contradictions >= 0),
            CONSTRAINT ck_table_column_mappings_layout CHECK (layout_key ~ '^[0-9a-f]{{40}}$'),
            CONSTRAINT ck_table_column_mappings_header CHECK (length(btrim(header_key)) > 0)
        )
        """
    )
    op.execute("CREATE INDEX ix_table_column_mappings_layout ON table_column_mappings (workspace_id, layout_key)")

    _visibility_check(internal_after_44())

    # -- the review hub learns TABLE ---------------------------------------------
    op.execute("ALTER TABLE review_assignments DROP CONSTRAINT ck_review_assignments_kind_known")
    op.execute(
        "ALTER TABLE review_assignments ADD CONSTRAINT ck_review_assignments_kind_known "
        f"CHECK (kind IN ({_in(REVIEW_KINDS)}))"
    )
    op.execute("DROP VIEW IF EXISTS review_queue_items")
    op.execute(review_queue_view_v5())


def _visibility_check(values: tuple[str, ...]) -> None:
    """ARCH-37's constraint, same shape byte for byte (arch40_step0._visibility_check)."""
    _load("arch40_step0_review_vocabulary")._visibility_check(values)


def downgrade() -> None:
    op.execute("DELETE FROM outbox_events WHERE event_type = 'trigger.table.flagged'")
    _visibility_check(tuple(_step43().internal_after_43()))
    op.execute("DROP VIEW IF EXISTS review_queue_items")
    op.execute(_step43().review_queue_view_v4())
    op.execute("DELETE FROM review_assignments WHERE kind = 'TABLE'")
    op.execute("ALTER TABLE review_assignments DROP CONSTRAINT ck_review_assignments_kind_known")
    op.execute(
        "ALTER TABLE review_assignments ADD CONSTRAINT ck_review_assignments_kind_known "
        f"CHECK (kind IN ({_in(REVIEW_KINDS_BEFORE)}))"
    )
    op.execute("DROP TRIGGER IF EXISTS trg_extracted_table_cells_within_grid ON extracted_table_cells")
    op.execute("DROP FUNCTION IF EXISTS extracted_table_cell_within_grid()")
    for table in TABLES_IN_DROP_ORDER:
        op.execute(f"DROP TABLE IF EXISTS {table}")

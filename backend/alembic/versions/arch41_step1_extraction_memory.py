"""ARCH-41 Step 1 — EXPAND: extraction memory.

Revision ID: arch41_step1_extraction_memory
Revises: hm1_tier_price_per_key

ARCH41-S2:migration. Expand only: seven new tables, nothing altered or dropped.

CHAIN POSITION
==============

Inserted between hm1_tier_price_per_key (the hardening-phase head) and
arch40_step3_contract_ai_settings (the flag-gated, lossy contract step),
exactly as hm1 itself was: the contract step's down_revision now names this
revision, so there is still ONE file head and `alembic upgrade head` still
refuses to run the contract without its flag. run_arch41.ps1 upgrades to this
revision by name, and handles a database whose contract step already ran
(stamp underneath it, apply, stamp back) the way run_hardening_master.ps1 did.

WHAT EACH TABLE IS FOR
======================

  extraction_memory_settings      per-workspace mode: OFF, SHADOW (learn and
                                  measure, never change an extraction) or AUTO
                                  (proven memory is applied). No row = SHADOW.
  extraction_templates            one row per recurring LAYOUT: a 384-d hashed
                                  signature of the document's header skeleton,
                                  HNSW-indexed for cosine search.
  extraction_template_members     which document belongs to which layout.
  extraction_exemplars            reviewed field values, harvested when a human
                                  resolves an extraction review. ENCRYPTED with
                                  the platform key set; sensitive fields keep
                                  only their shape (AAAAA9999A), never a value.
  extraction_anchor_rules         deterministic "value sits N tokens after this
                                  label" rules, promoted only when their replay
                                  precision's Wilson lower bound is >= 0.95.
  extraction_memory_trials        randomized trials: memory must PROVE it cuts
                                  corrections (one-sided Mann-Whitney U) before
                                  a layout's memory is applied without a trial.
  extraction_memory_applications  provenance: what memory did to each document,
                                  and the review outcome that judged it.

TENANT ISOLATION IS DECLARATIVE
===============================

Every child row carries workspace_id and a COMPOSITE foreign key onto its
parent's (id, workspace_id) — work_items' uq_work_items_id_workspace_id
(ARCH-38) and the templates' own unique key. An exemplar that names a template
in another workspace is refused by PostgreSQL with 23503, not by a query
filter someone could forget.

THE CONTROL ARM IS PROTECTED BY THE SCHEMA
==========================================

ck_extraction_memory_applications_injected_arm: a document may only have
received memory if its arm is ACTIVE or TRIAL_ON. A bug that leaked memory into
the control arm would bias every trial toward "memory helps"; the database
refuses the row instead.

Constraint names are written raw (`ALTER TABLE ... ADD CONSTRAINT <name>`)
for the reason ARCH-31/34/37/38/40 give: the metadata naming convention would
otherwise rename them and the gates look for the documented names.
"""

from __future__ import annotations

from alembic import op

revision = "arch41_step1_extraction_memory"
down_revision = "hm1_tier_price_per_key"
branch_labels = None
depends_on = None

SIGNATURE_DIM = 384

TABLES_IN_DROP_ORDER: tuple[str, ...] = (
    "extraction_memory_applications",
    "extraction_memory_trials",
    "extraction_anchor_rules",
    "extraction_exemplars",
    "extraction_template_members",
    "extraction_templates",
    "extraction_memory_settings",
)

TS = "timestamptz NOT NULL DEFAULT now()"


def upgrade() -> None:
    op.execute(
        f"""
        CREATE TABLE extraction_memory_settings (
            workspace_id uuid PRIMARY KEY,
            organization_id uuid NOT NULL,
            mode varchar(8) NOT NULL DEFAULT 'SHADOW',
            updated_by_user_id uuid REFERENCES users (id) ON DELETE SET NULL,
            created_at {TS},
            updated_at {TS},
            CONSTRAINT ck_extraction_memory_settings_mode CHECK (mode IN ('OFF', 'SHADOW', 'AUTO')),
            CONSTRAINT fk_extraction_memory_settings_workspace_org FOREIGN KEY (workspace_id, organization_id)
                REFERENCES workspaces (id, organization_id) ON DELETE CASCADE
        )
        """
    )

    op.execute(
        f"""
        CREATE TABLE extraction_templates (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id uuid NOT NULL,
            workspace_id uuid NOT NULL,
            document_type varchar(64) NOT NULL,
            signature vector({SIGNATURE_DIM}) NOT NULL,
            anchor_tokens text[] NOT NULL DEFAULT '{{}}',
            member_count integer NOT NULL DEFAULT 0,
            state varchar(10) NOT NULL DEFAULT 'LEARNING',
            activated_at timestamptz,
            created_at {TS},
            updated_at {TS},
            CONSTRAINT uq_extraction_templates_id_workspace UNIQUE (id, workspace_id),
            CONSTRAINT ck_extraction_templates_member_count CHECK (member_count >= 0),
            CONSTRAINT ck_extraction_templates_state CHECK (state IN ('LEARNING', 'TRIAL', 'ACTIVE', 'REJECTED')),
            CONSTRAINT ck_extraction_templates_active_has_time CHECK (state <> 'ACTIVE' OR activated_at IS NOT NULL),
            CONSTRAINT ck_extraction_templates_document_type CHECK (length(btrim(document_type)) > 0),
            CONSTRAINT fk_extraction_templates_workspace_org FOREIGN KEY (workspace_id, organization_id)
                REFERENCES workspaces (id, organization_id) ON DELETE CASCADE
        )
        """
    )
    op.execute("CREATE INDEX ix_extraction_templates_workspace_type ON extraction_templates (workspace_id, document_type)")
    op.execute(
        "CREATE INDEX ix_extraction_templates_signature_hnsw ON extraction_templates "
        "USING hnsw (signature vector_cosine_ops)"
    )

    op.execute(
        f"""
        CREATE TABLE extraction_template_members (
            work_item_id uuid PRIMARY KEY,
            workspace_id uuid NOT NULL,
            template_id uuid NOT NULL,
            similarity numeric(5,4) NOT NULL,
            created_at {TS},
            CONSTRAINT ck_extraction_template_members_similarity CHECK (similarity >= 0 AND similarity <= 1),
            CONSTRAINT fk_extraction_template_members_template FOREIGN KEY (template_id, workspace_id)
                REFERENCES extraction_templates (id, workspace_id) ON DELETE CASCADE,
            CONSTRAINT fk_extraction_template_members_work_item FOREIGN KEY (work_item_id, workspace_id)
                REFERENCES work_items (id, workspace_id) ON DELETE CASCADE
        )
        """
    )
    op.execute("CREATE INDEX ix_extraction_template_members_template ON extraction_template_members (template_id)")

    op.execute(
        f"""
        CREATE TABLE extraction_exemplars (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            workspace_id uuid NOT NULL,
            template_id uuid NOT NULL,
            work_item_id uuid NOT NULL,
            verification_field_id uuid NOT NULL REFERENCES document_verification_fields (id) ON DELETE CASCADE,
            field_path varchar(200) NOT NULL,
            label_source varchar(10) NOT NULL,
            value_form varchar(5) NOT NULL,
            value_ciphertext text NOT NULL,
            evidence_ciphertext text,
            key_fingerprint varchar(64) NOT NULL,
            value_token_count integer NOT NULL DEFAULT 1,
            line_index integer,
            token_index integer,
            page_number integer,
            bbox numeric(9,2)[],
            created_at {TS},
            CONSTRAINT uq_extraction_exemplars_verification_field UNIQUE (verification_field_id),
            CONSTRAINT uq_extraction_exemplars_item_field UNIQUE (work_item_id, field_path),
            CONSTRAINT ck_extraction_exemplars_label_source CHECK (label_source IN ('CORRECTED', 'CONFIRMED')),
            CONSTRAINT ck_extraction_exemplars_value_form CHECK (value_form IN ('RAW', 'SHAPE')),
            CONSTRAINT ck_extraction_exemplars_page CHECK (page_number IS NULL OR page_number >= 1),
            CONSTRAINT ck_extraction_exemplars_bbox CHECK (bbox IS NULL OR array_length(bbox, 1) = 4),
            CONSTRAINT ck_extraction_exemplars_token_count CHECK (value_token_count >= 1),
            CONSTRAINT fk_extraction_exemplars_template FOREIGN KEY (template_id, workspace_id)
                REFERENCES extraction_templates (id, workspace_id) ON DELETE CASCADE,
            CONSTRAINT fk_extraction_exemplars_work_item FOREIGN KEY (work_item_id, workspace_id)
                REFERENCES work_items (id, workspace_id) ON DELETE CASCADE
        )
        """
    )
    op.execute("CREATE INDEX ix_extraction_exemplars_template_created ON extraction_exemplars (template_id, created_at DESC)")

    op.execute(
        f"""
        CREATE TABLE extraction_anchor_rules (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            workspace_id uuid NOT NULL,
            template_id uuid NOT NULL,
            field_path varchar(200) NOT NULL,
            anchor_norm varchar(120) NOT NULL,
            offset_dx integer NOT NULL,
            offset_dy integer NOT NULL,
            value_token_count integer NOT NULL DEFAULT 1,
            value_shape varchar(64),
            support integer NOT NULL,
            replay_hits integer NOT NULL DEFAULT 0,
            replay_total integer NOT NULL DEFAULT 0,
            wilson_lower numeric(5,4),
            live_hits integer NOT NULL DEFAULT 0,
            live_total integer NOT NULL DEFAULT 0,
            state varchar(8) NOT NULL DEFAULT 'SHADOW',
            activated_at timestamptz,
            retired_at timestamptz,
            retired_reason varchar(32),
            created_at {TS},
            updated_at {TS},
            CONSTRAINT uq_extraction_anchor_rules_identity UNIQUE (template_id, field_path, anchor_norm, offset_dx, offset_dy),
            CONSTRAINT ck_extraction_anchor_rules_support CHECK (support >= 3),
            CONSTRAINT ck_extraction_anchor_rules_replay CHECK (replay_hits >= 0 AND replay_hits <= replay_total),
            CONSTRAINT ck_extraction_anchor_rules_live CHECK (live_hits >= 0 AND live_hits <= live_total),
            CONSTRAINT ck_extraction_anchor_rules_state CHECK (state IN ('SHADOW', 'ACTIVE', 'RETIRED')),
            CONSTRAINT ck_extraction_anchor_rules_active_has_time CHECK (state <> 'ACTIVE' OR activated_at IS NOT NULL),
            CONSTRAINT ck_extraction_anchor_rules_retired_has_reason CHECK (state <> 'RETIRED' OR retired_reason IS NOT NULL),
            CONSTRAINT ck_extraction_anchor_rules_active_is_proven CHECK (state <> 'ACTIVE' OR wilson_lower >= 0.95),
            CONSTRAINT ck_extraction_anchor_rules_offsets CHECK (offset_dy BETWEEN 0 AND 3 AND offset_dx BETWEEN -8 AND 8),
            CONSTRAINT ck_extraction_anchor_rules_token_count CHECK (value_token_count BETWEEN 1 AND 12),
            CONSTRAINT ck_extraction_anchor_rules_wilson CHECK (wilson_lower IS NULL OR (wilson_lower >= 0 AND wilson_lower <= 1)),
            CONSTRAINT fk_extraction_anchor_rules_template FOREIGN KEY (template_id, workspace_id)
                REFERENCES extraction_templates (id, workspace_id) ON DELETE CASCADE
        )
        """
    )

    op.execute(
        f"""
        CREATE TABLE extraction_memory_trials (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            workspace_id uuid NOT NULL,
            template_id uuid NOT NULL,
            state varchar(9) NOT NULL DEFAULT 'RUNNING',
            on_docs integer NOT NULL DEFAULT 0,
            off_docs integer NOT NULL DEFAULT 0,
            on_correction_rate numeric(6,5),
            off_correction_rate numeric(6,5),
            u_statistic numeric(14,2),
            p_value numeric(8,6),
            decision_reason varchar(200),
            started_at {TS},
            decided_at timestamptz,
            CONSTRAINT uq_extraction_memory_trials_id_workspace UNIQUE (id, workspace_id),
            CONSTRAINT ck_extraction_memory_trials_state CHECK (state IN ('RUNNING', 'PROMOTED', 'REJECTED', 'ABANDONED')),
            CONSTRAINT ck_extraction_memory_trials_decided CHECK ((state = 'RUNNING') = (decided_at IS NULL)),
            CONSTRAINT ck_extraction_memory_trials_docs CHECK (on_docs >= 0 AND off_docs >= 0),
            CONSTRAINT ck_extraction_memory_trials_p CHECK (p_value IS NULL OR (p_value >= 0 AND p_value <= 1)),
            CONSTRAINT ck_extraction_memory_trials_rates CHECK (
                (on_correction_rate IS NULL OR (on_correction_rate >= 0 AND on_correction_rate <= 1)) AND
                (off_correction_rate IS NULL OR (off_correction_rate >= 0 AND off_correction_rate <= 1))),
            CONSTRAINT fk_extraction_memory_trials_template FOREIGN KEY (template_id, workspace_id)
                REFERENCES extraction_templates (id, workspace_id) ON DELETE CASCADE
        )
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_extraction_memory_trials_one_running ON extraction_memory_trials (template_id) "
        "WHERE state = 'RUNNING'"
    )

    op.execute(
        f"""
        CREATE TABLE extraction_memory_applications (
            work_item_id uuid PRIMARY KEY,
            workspace_id uuid NOT NULL,
            template_id uuid,
            trial_id uuid,
            arm varchar(9) NOT NULL,
            injected boolean NOT NULL DEFAULT false,
            exemplar_ids uuid[] NOT NULL DEFAULT '{{}}',
            anchor_rule_ids uuid[] NOT NULL DEFAULT '{{}}',
            anchor_candidates jsonb NOT NULL DEFAULT '{{}}'::jsonb,
            agreed_fields text[] NOT NULL DEFAULT '{{}}',
            disagreed_fields text[] NOT NULL DEFAULT '{{}}',
            memory_tokens integer NOT NULL DEFAULT 0,
            fields_total integer,
            fields_corrected integer,
            corrected_fields text[] NOT NULL DEFAULT '{{}}',
            outcome_recorded_at timestamptz,
            created_at {TS},
            updated_at {TS},
            CONSTRAINT ck_extraction_memory_applications_arm CHECK (arm IN ('ACTIVE', 'TRIAL_ON', 'TRIAL_OFF', 'SHADOW', 'NONE')),
            CONSTRAINT ck_extraction_memory_applications_trial_arm CHECK (arm NOT IN ('TRIAL_ON', 'TRIAL_OFF') OR trial_id IS NOT NULL),
            CONSTRAINT ck_extraction_memory_applications_injected_arm CHECK (NOT injected OR arm IN ('ACTIVE', 'TRIAL_ON')),
            CONSTRAINT ck_extraction_memory_applications_outcome CHECK (
                fields_total IS NULL OR (fields_total >= 0 AND fields_corrected >= 0 AND fields_corrected <= fields_total)),
            CONSTRAINT ck_extraction_memory_applications_tokens CHECK (memory_tokens >= 0),
            CONSTRAINT fk_extraction_memory_applications_work_item FOREIGN KEY (work_item_id, workspace_id)
                REFERENCES work_items (id, workspace_id) ON DELETE CASCADE,
            CONSTRAINT fk_extraction_memory_applications_template FOREIGN KEY (template_id, workspace_id)
                REFERENCES extraction_templates (id, workspace_id) ON DELETE CASCADE,
            CONSTRAINT fk_extraction_memory_applications_trial FOREIGN KEY (trial_id, workspace_id)
                REFERENCES extraction_memory_trials (id, workspace_id) ON DELETE CASCADE
        )
        """
    )
    op.execute("CREATE INDEX ix_extraction_memory_applications_template_arm ON extraction_memory_applications (template_id, arm)")
    op.execute("CREATE INDEX ix_extraction_memory_applications_trial ON extraction_memory_applications (trial_id)")


def downgrade() -> None:
    for table in TABLES_IN_DROP_ORDER:
        op.execute(f"DROP TABLE IF EXISTS {table}")

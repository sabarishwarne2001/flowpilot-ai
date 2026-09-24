"""ARCH-42 Step 1 — EXPAND: entity resolution and the document knowledge graph.

Revision ID: arch42_step1_entity_graph
Revises: arch41_step1_extraction_memory

ARCH42-S1:migration. Expand only. Six new tables; the review hub's view and its
assignment CHECK learn a fourth kind (MERGE); the eleven platform presets gain
`x-entity` annotations. Nothing existing is dropped or narrowed.

CHAIN POSITION
==============

Inserted between arch41_step1_extraction_memory (the ARCH-41 release head) and
arch40_step3_contract_ai_settings (the flag-gated, lossy contract step), as
hm1 and ARCH-41 were: the contract step's down_revision now names this
revision, so there is still ONE file head and `alembic upgrade head` still
refuses the contract without ARCH40_CONTRACT. run_arch42.ps1 upgrades to this
revision by name and handles a database whose contract already ran.

THE TABLES
==========

  entities                  one canonical record per person, organization,
                            address, account, asset or shipment. A merge is a
                            POINTER (merged_into_id), never a rewrite, so every
                            merge is reversible by clearing it.
  entity_identifiers        hard and soft identifiers as HMAC + encrypted
                            masked display (+ encrypted value, for re-keying,
                            never for Aadhaar). No plaintext identifier.
  entity_mentions           which field of which document named which record,
                            with the decision (AUTO/REVIEW/CONFIRMED/REJECTED)
                            and the evidence identifiers it contributed.
  entity_edges              relationships, one row per evidencing document.
  entity_match_models       per-workspace, per-kind Fellegi-Sunter parameters,
                            versioned; one ACTIVE per kind.
  entity_merge_candidates   the MERGE review kind: pairs the conflict guard or
                            the model sent to a human, and pairs a human
                            decided (SEPARATE is remembered, so a nightly sweep
                            never re-proposes what a person rejected).

The roadmap listed five tables. The sixth is the review hub's source: a guard
refusal found by the nightly sweep is a pair of RECORDS, not a mention, and a
view cannot project a row that exists nowhere.

ISOLATION IS DECLARATIVE
========================

Every reference between these tables is a COMPOSITE foreign key onto
(id, workspace_id): a merge into another workspace's record, a mention of
another workspace's document, an edge between two workspaces — each is refused
by PostgreSQL with 23503, not by a filter someone could forget.

NO PLAINTEXT IDENTIFIER, BY CONSTRAINT
======================================

value_hmac must be 64 lowercase hex; both ciphertext columns must be Fernet
tokens ('gAAAAA' prefix); an AADHAAR row may hold no value at all. The gates
also scan every text column of every entity table for the planted values.

Constraint names are written raw for the reason ARCH-31..41 give.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import sqlalchemy as sa
from alembic import op

revision = "arch42_step1_entity_graph"
down_revision = "arch41_step1_extraction_memory"
branch_labels = None
depends_on = None

EMBEDDING_DIM = 256

ENTITY_KINDS = ("PERSON", "ORGANIZATION", "ADDRESS", "ACCOUNT", "ASSET", "SHIPMENT")
IDENTIFIER_KINDS = (
    "EMAIL", "PHONE", "PAN", "GSTIN", "IBAN", "PASSPORT", "AADHAAR", "DATE_OF_BIRTH",
    "MEDICAL_RECORD", "ACCOUNT_NUMBER", "CONTAINER_NUMBER", "BL_NUMBER", "CUSTOMS_DECLARATION",
)
HARD_IDENTIFIER_KINDS = (
    "EMAIL", "PAN", "GSTIN", "IBAN", "PASSPORT", "AADHAAR", "ACCOUNT_NUMBER",
    "CONTAINER_NUMBER", "BL_NUMBER", "CUSTOMS_DECLARATION",
)
RELATIONS = (
    "EMPLOYED_BY", "CONTRACTED_WITH", "SUPPLIES", "LEASES_FROM", "OCCUPIES", "LESSOR_OF",
    "SHIPPED", "CONSIGNED_TO", "CONTAINS", "HOLDS_ACCOUNT", "LOCATED_AT",
)
DECISIONS = ("AUTO", "REVIEW", "CONFIRMED", "REJECTED")
METHODS = ("IDENTIFIER", "MODEL", "NEW", "MANUAL")
SOURCES = ("PRESET", "BUILTIN", "DETECTOR")
MERGE_REASONS = ("IDENTIFIER", "MODEL", "REVIEW", "MANUAL")
CANDIDATE_REASONS = ("CONFLICT", "UNCERTAIN", "MANUAL")
CANDIDATE_STATUSES = ("OPEN", "MERGED", "SEPARATE", "OBSOLETE")

#: ARCH42-S1:review-kinds. The hub's kinds after ARCH-42 (ARCH-40's three + MERGE).
REVIEW_KINDS = ("EXTRACTION", "ASSERTION", "ANOMALY", "MERGE")
REVIEW_KINDS_BEFORE = ("EXTRACTION", "ASSERTION", "ANOMALY")
#: ARCH42-S1:review-reasons. ARCH-40's seven + ENTITY_MERGE.
REVIEW_REASONS = (
    "DISAGREEMENT", "ESCALATION", "CALIBRATION_HOLD", "AUTONOMY_AUDIT",
    "PENDING_REVIEW", "CLAUSE_TRIAGE", "ANOMALY", "ENTITY_MERGE",
)

TABLES_IN_DROP_ORDER = (
    "entity_merge_candidates",
    "entity_edges",
    "entity_mentions",
    "entity_identifiers",
    "entities",
    "entity_match_models",
)

TS = "timestamptz NOT NULL DEFAULT now()"
FERNET = "LIKE 'gAAAAA%'"


def _in(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{x}'" for x in values)


def _step2a():
    spec = importlib.util.spec_from_file_location(
        "arch40_step2a_review_view_paths", Path(__file__).with_name("arch40_step2a_review_view_paths.py"))
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


#: ARCH42-S1:review-view-v3. ARCH-40's view, unchanged, plus a MERGE arm. The
#: earlier arms are ARCH-40's text itself (loaded, not copied), so they cannot
#: drift from the definition verify_arch40 checks.
MERGE_ARM = """
    UNION ALL

    SELECT
        'MERGE'::varchar(16),
        mc.id,
        mc.organization_id,
        mc.workspace_id,
        mc.work_item_id,
        (CASE WHEN mc.reason = 'CONFLICT'
              THEN 'Same ' || lower(mc.entity_kind) || ' with conflicting identifiers? '
              ELSE 'Same ' || lower(mc.entity_kind) || '? '
         END || le.display_name || ' / ' || re.display_name)::text,
        CASE WHEN mc.reason = 'CONFLICT' THEN 'HIGH' ELSE 'MEDIUM' END::varchar(8),
        CASE WHEN mc.reason = 'CONFLICT' THEN 2 ELSE 3 END::integer,
        mc.match_probability,
        mc.created_at,
        CASE WHEN mc.status = 'OPEN' THEN 'OPEN' ELSE 'RESOLVED' END::varchar(8),
        mc.resolved_at,
        mc.resolved_by_user_id,
        'ENTITY_MERGE'::varchar(24)
    FROM entity_merge_candidates mc
    JOIN entities le ON le.id = mc.left_entity_id
    JOIN entities re ON re.id = mc.right_entity_id
    WHERE mc.status IN ('OPEN', 'MERGED', 'SEPARATE')
"""


def review_queue_view_v3() -> str:
    return _step2a().REVIEW_QUEUE_VIEW_V2.rstrip() + "\n" + MERGE_ARM


#: ARCH42-S1:preset-annotations-sql. Generated from
#: app/services/entities/annotations.PRESET_ENTITY_ANNOTATIONS; verify_arch42
#: gate E2 asserts the two are identical.
PRESET_ANNOTATIONS_JSON = r"""{
 "bill_of_lading": {
  "properties": {
   "bl_number": {
    "identifier": "BL_NUMBER",
    "kind": "SHIPMENT",
    "role": "shipment"
   },
   "consignee": {
    "edges": [
     {
      "from": "bl_number",
      "relation": "CONSIGNED_TO"
     }
    ],
    "kind": "PARTY",
    "role": "consignee"
   },
   "container_numbers": {
    "edges": [
     {
      "from": "bl_number",
      "relation": "CONTAINS"
     }
    ],
    "identifier": "CONTAINER_NUMBER",
    "kind": "ASSET",
    "role": "container"
   },
   "shipper": {
    "edges": [
     {
      "relation": "SHIPPED",
      "to": "bl_number"
     }
    ],
    "kind": "PARTY",
    "role": "shipper"
   }
  }
 },
 "customs_manifest": {
  "properties": {
   "declaration_number": {
    "identifier": "CUSTOMS_DECLARATION",
    "kind": "SHIPMENT",
    "role": "declaration"
   }
  }
 },
 "discharge_summary": {
  "properties": {
   "patient_name": {
    "kind": "PERSON",
    "role": "patient"
   }
  }
 },
 "india_id_card": {
  "detect": {
   "identifiers": [
    "AADHAAR",
    "PAN"
   ],
   "of": "holder_name"
  },
  "properties": {
   "date_of_birth": {
    "identifier": "DATE_OF_BIRTH",
    "of": "holder_name"
   },
   "holder_name": {
    "kind": "PERSON",
    "role": "holder"
   }
  }
 },
 "intake_form": {
  "properties": {
   "date_of_birth": {
    "identifier": "DATE_OF_BIRTH",
    "of": "patient_name"
   },
   "patient_name": {
    "kind": "PERSON",
    "role": "patient"
   },
   "record_number": {
    "identifier": "MEDICAL_RECORD",
    "of": "patient_name"
   }
  }
 },
 "lease": {
  "properties": {
   "landlord": {
    "kind": "PARTY",
    "role": "landlord"
   },
   "premises": {
    "edges": [
     {
      "from": "tenant",
      "relation": "OCCUPIES"
     },
     {
      "from": "landlord",
      "relation": "LESSOR_OF"
     }
    ],
    "kind": "ADDRESS",
    "role": "premises"
   },
   "tenant": {
    "edges": [
     {
      "relation": "LEASES_FROM",
      "to": "landlord"
     }
    ],
    "kind": "PARTY",
    "role": "tenant"
   }
  }
 },
 "msa": {
  "properties": {
   "customer": {
    "kind": "PARTY",
    "role": "customer"
   },
   "supplier": {
    "edges": [
     {
      "relation": "SUPPLIES",
      "to": "customer"
     }
    ],
    "kind": "PARTY",
    "role": "supplier"
   }
  }
 },
 "nda": {
  "properties": {
   "disclosing_party": {
    "kind": "PARTY",
    "role": "disclosing_party"
   },
   "receiving_party": {
    "edges": [
     {
      "from": "disclosing_party",
      "relation": "CONTRACTED_WITH"
     }
    ],
    "kind": "PARTY",
    "role": "receiving_party"
   }
  }
 },
 "offer_letter": {
  "properties": {
   "candidate_name": {
    "kind": "PERSON",
    "role": "candidate"
   }
  }
 },
 "passport": {
  "properties": {
   "holder_name": {
    "kind": "PERSON",
    "role": "holder"
   },
   "passport_number": {
    "identifier": "PASSPORT",
    "of": "holder_name"
   }
  }
 },
 "resume": {
  "properties": {
   "candidate_name": {
    "kind": "PERSON",
    "role": "candidate"
   },
   "email": {
    "identifier": "EMAIL",
    "of": "candidate_name"
   },
   "most_recent_employer": {
    "edges": [
     {
      "from": "candidate_name",
      "relation": "EMPLOYED_BY"
     }
    ],
    "kind": "ORGANIZATION",
    "role": "employer"
   },
   "phone": {
    "identifier": "PHONE",
    "of": "candidate_name"
   }
  }
 }
}"""


def _annotate_presets(add: bool) -> None:
    connection = op.get_bind()
    annotations = json.loads(PRESET_ANNOTATIONS_JSON)
    rows = connection.execute(sa.text(
        "SELECT id, document_type, schema FROM document_schema_presets WHERE organization_id IS NULL"
    )).all()
    for row in rows:
        entry = annotations.get(row.document_type)
        if entry is None:
            continue
        schema = row.schema if isinstance(row.schema, dict) else json.loads(row.schema)
        properties = schema.get("properties") or {}
        for field, annotation in entry["properties"].items():
            if field in properties:
                if add:
                    properties[field]["x-entity"] = annotation
                else:
                    properties[field].pop("x-entity", None)
        if add and entry.get("detect"):
            schema["x-entity-detect"] = entry["detect"]
        elif not add:
            schema.pop("x-entity-detect", None)
        connection.execute(
            sa.text("UPDATE document_schema_presets SET schema = CAST(:schema AS jsonb) WHERE id = :id"),
            {"schema": json.dumps(schema), "id": row.id},
        )


def upgrade() -> None:
    op.execute(
        f"""
        CREATE TABLE entity_match_models (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id uuid NOT NULL,
            workspace_id uuid NOT NULL,
            entity_kind varchar(16) NOT NULL,
            version integer NOT NULL,
            status varchar(8) NOT NULL DEFAULT 'ACTIVE',
            source varchar(8) NOT NULL,
            parameters jsonb NOT NULL,
            pair_count integer NOT NULL DEFAULT 0,
            iterations integer NOT NULL DEFAULT 0,
            converged boolean NOT NULL DEFAULT false,
            log_likelihood double precision,
            fitted_at {TS},
            CONSTRAINT uq_entity_match_models_version UNIQUE (workspace_id, entity_kind, version),
            CONSTRAINT ck_entity_match_models_kind CHECK (entity_kind IN ('PERSON', 'ORGANIZATION', 'ADDRESS')),
            CONSTRAINT ck_entity_match_models_status CHECK (status IN ('ACTIVE', 'RETIRED')),
            CONSTRAINT ck_entity_match_models_source CHECK (source IN ('PRIOR', 'FITTED')),
            CONSTRAINT ck_entity_match_models_version CHECK (version >= 1),
            CONSTRAINT ck_entity_match_models_parameters CHECK (jsonb_typeof(parameters) = 'object'
                AND parameters ? 'lambda' AND parameters ? 'm' AND parameters ? 'u'),
            CONSTRAINT ck_entity_match_models_fitted_converged CHECK (source <> 'FITTED' OR converged),
            CONSTRAINT ck_entity_match_models_pairs CHECK (pair_count >= 0 AND iterations >= 0),
            CONSTRAINT fk_entity_match_models_workspace_org FOREIGN KEY (workspace_id, organization_id)
                REFERENCES workspaces (id, organization_id) ON DELETE CASCADE
        )
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_entity_match_models_one_active ON entity_match_models "
        "(workspace_id, entity_kind) WHERE status = 'ACTIVE'"
    )

    op.execute(
        f"""
        CREATE TABLE entities (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id uuid NOT NULL,
            workspace_id uuid NOT NULL,
            kind varchar(16) NOT NULL,
            status varchar(8) NOT NULL DEFAULT 'ACTIVE',
            display_name varchar(300) NOT NULL,
            normalized_name varchar(300) NOT NULL,
            name_embedding vector({EMBEDDING_DIM}),
            merged_into_id uuid,
            merged_at timestamptz,
            merged_by_user_id uuid REFERENCES users (id) ON DELETE SET NULL,
            merge_reason varchar(12),
            split_from_id uuid,
            mention_count integer NOT NULL DEFAULT 0,
            first_seen_at timestamptz,
            last_seen_at timestamptz,
            created_at {TS},
            updated_at {TS},
            CONSTRAINT uq_entities_id_workspace UNIQUE (id, workspace_id),
            CONSTRAINT ck_entities_kind CHECK (kind IN ({_in(ENTITY_KINDS)})),
            CONSTRAINT ck_entities_status CHECK (status IN ('ACTIVE', 'MERGED')),
            CONSTRAINT ck_entities_merged_pointer CHECK ((status = 'MERGED') = (merged_into_id IS NOT NULL)),
            CONSTRAINT ck_entities_merged_has_time CHECK (merged_into_id IS NULL OR (merged_at IS NOT NULL AND merge_reason IS NOT NULL)),
            CONSTRAINT ck_entities_merge_reason CHECK (merge_reason IS NULL OR merge_reason IN ({_in(MERGE_REASONS)})),
            CONSTRAINT ck_entities_not_self CHECK (merged_into_id IS NULL OR merged_into_id <> id),
            CONSTRAINT ck_entities_split_not_self CHECK (split_from_id IS NULL OR split_from_id <> id),
            CONSTRAINT ck_entities_name CHECK (length(btrim(normalized_name)) > 0 AND length(btrim(display_name)) > 0),
            CONSTRAINT ck_entities_mention_count CHECK (mention_count >= 0),
            CONSTRAINT fk_entities_workspace_org FOREIGN KEY (workspace_id, organization_id)
                REFERENCES workspaces (id, organization_id) ON DELETE CASCADE,
            CONSTRAINT fk_entities_merged_into FOREIGN KEY (merged_into_id, workspace_id)
                REFERENCES entities (id, workspace_id),
            CONSTRAINT fk_entities_split_from FOREIGN KEY (split_from_id, workspace_id)
                REFERENCES entities (id, workspace_id) ON DELETE SET NULL (split_from_id)
        )
        """
    )
    op.execute("CREATE INDEX ix_entities_workspace_kind_status ON entities (workspace_id, kind, status)")
    op.execute("CREATE INDEX ix_entities_merged_into ON entities (merged_into_id) WHERE merged_into_id IS NOT NULL")
    op.execute("CREATE INDEX ix_entities_normalized_name_trgm ON entities USING gin (normalized_name gin_trgm_ops)")
    op.execute("CREATE INDEX ix_entities_name_embedding_hnsw ON entities USING hnsw (name_embedding vector_cosine_ops)")

    op.execute(
        f"""
        CREATE TABLE entity_identifiers (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            workspace_id uuid NOT NULL,
            entity_id uuid NOT NULL,
            entity_kind varchar(16) NOT NULL,
            kind varchar(24) NOT NULL,
            value_hmac varchar(64) NOT NULL,
            key_generation varchar(16) NOT NULL,
            value_ciphertext text,
            display_ciphertext text NOT NULL,
            derived boolean NOT NULL DEFAULT false,
            created_at {TS},
            updated_at {TS},
            CONSTRAINT ck_entity_identifiers_kind CHECK (kind IN ({_in(IDENTIFIER_KINDS)})),
            CONSTRAINT ck_entity_identifiers_entity_kind CHECK (entity_kind IN ({_in(ENTITY_KINDS)})),
            CONSTRAINT ck_entity_identifiers_hmac_hex CHECK (value_hmac ~ '^[0-9a-f]{{64}}$'),
            CONSTRAINT ck_entity_identifiers_display_sealed CHECK (display_ciphertext {FERNET}),
            CONSTRAINT ck_entity_identifiers_value_sealed CHECK (value_ciphertext IS NULL OR value_ciphertext {FERNET}),
            CONSTRAINT ck_entity_identifiers_aadhaar_no_value CHECK (kind <> 'AADHAAR' OR value_ciphertext IS NULL),
            CONSTRAINT ck_entity_identifiers_derived_pan CHECK (NOT derived OR kind = 'PAN'),
            CONSTRAINT uq_entity_identifiers_entity_value UNIQUE (entity_id, kind, value_hmac),
            CONSTRAINT fk_entity_identifiers_entity FOREIGN KEY (entity_id, workspace_id)
                REFERENCES entities (id, workspace_id) ON DELETE CASCADE
        )
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_entity_identifiers_hard_value ON entity_identifiers "
        f"(workspace_id, entity_kind, kind, value_hmac) WHERE kind IN ({_in(HARD_IDENTIFIER_KINDS)})"
    )
    op.execute("CREATE INDEX ix_entity_identifiers_lookup ON entity_identifiers (workspace_id, kind, value_hmac)")
    op.execute("CREATE INDEX ix_entity_identifiers_generation ON entity_identifiers (key_generation)")

    op.execute(
        f"""
        CREATE TABLE entity_mentions (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            workspace_id uuid NOT NULL,
            work_item_id uuid NOT NULL,
            entity_id uuid NOT NULL,
            entity_kind varchar(16) NOT NULL,
            field_path varchar(200) NOT NULL,
            ordinal integer NOT NULL DEFAULT 0,
            role varchar(48) NOT NULL,
            surface_name varchar(300) NOT NULL,
            spec_digest varchar(64) NOT NULL,
            identifier_ids uuid[] NOT NULL DEFAULT '{{}}',
            decision varchar(10) NOT NULL,
            method varchar(12) NOT NULL,
            source varchar(8) NOT NULL,
            match_probability numeric(6,5),
            match_weight numeric(9,3),
            decided_by_user_id uuid REFERENCES users (id) ON DELETE SET NULL,
            created_at {TS},
            updated_at {TS},
            CONSTRAINT uq_entity_mentions_field UNIQUE (work_item_id, field_path, ordinal),
            CONSTRAINT ck_entity_mentions_decision CHECK (decision IN ({_in(DECISIONS)})),
            CONSTRAINT ck_entity_mentions_method CHECK (method IN ({_in(METHODS)})),
            CONSTRAINT ck_entity_mentions_source CHECK (source IN ({_in(SOURCES)})),
            CONSTRAINT ck_entity_mentions_kind CHECK (entity_kind IN ({_in(ENTITY_KINDS)})),
            CONSTRAINT ck_entity_mentions_probability CHECK (match_probability IS NULL OR (match_probability >= 0 AND match_probability <= 1)),
            CONSTRAINT ck_entity_mentions_ordinal CHECK (ordinal >= 0),
            CONSTRAINT ck_entity_mentions_digest CHECK (spec_digest ~ '^[0-9a-f]{{64}}$'),
            CONSTRAINT ck_entity_mentions_human_decided CHECK (decision NOT IN ('CONFIRMED', 'REJECTED') OR method = 'MANUAL' OR decided_by_user_id IS NOT NULL),
            CONSTRAINT fk_entity_mentions_work_item FOREIGN KEY (work_item_id, workspace_id)
                REFERENCES work_items (id, workspace_id) ON DELETE CASCADE,
            CONSTRAINT fk_entity_mentions_entity FOREIGN KEY (entity_id, workspace_id)
                REFERENCES entities (id, workspace_id) ON DELETE CASCADE
        )
        """
    )
    op.execute("CREATE INDEX ix_entity_mentions_entity ON entity_mentions (entity_id)")
    op.execute("CREATE INDEX ix_entity_mentions_workspace_decision ON entity_mentions (workspace_id, decision)")
    op.execute("CREATE INDEX ix_entity_mentions_identifiers ON entity_mentions USING gin (identifier_ids)")

    op.execute(
        f"""
        CREATE TABLE entity_edges (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            workspace_id uuid NOT NULL,
            src_entity_id uuid NOT NULL,
            dst_entity_id uuid NOT NULL,
            relation varchar(24) NOT NULL,
            evidence_work_item_id uuid NOT NULL,
            created_at {TS},
            CONSTRAINT uq_entity_edges_src_dst_relation_evidence UNIQUE (src_entity_id, dst_entity_id, relation, evidence_work_item_id),
            CONSTRAINT ck_entity_edges_relation CHECK (relation IN ({_in(RELATIONS)})),
            CONSTRAINT ck_entity_edges_not_self CHECK (src_entity_id <> dst_entity_id),
            CONSTRAINT fk_entity_edges_src FOREIGN KEY (src_entity_id, workspace_id)
                REFERENCES entities (id, workspace_id) ON DELETE CASCADE,
            CONSTRAINT fk_entity_edges_dst FOREIGN KEY (dst_entity_id, workspace_id)
                REFERENCES entities (id, workspace_id) ON DELETE CASCADE,
            CONSTRAINT fk_entity_edges_evidence FOREIGN KEY (evidence_work_item_id, workspace_id)
                REFERENCES work_items (id, workspace_id) ON DELETE CASCADE
        )
        """
    )
    op.execute("CREATE INDEX ix_entity_edges_dst ON entity_edges (dst_entity_id)")
    op.execute("CREATE INDEX ix_entity_edges_evidence ON entity_edges (evidence_work_item_id)")

    op.execute(
        f"""
        CREATE TABLE entity_merge_candidates (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id uuid NOT NULL,
            workspace_id uuid NOT NULL,
            left_entity_id uuid NOT NULL,
            right_entity_id uuid NOT NULL,
            low_entity_id uuid GENERATED ALWAYS AS (LEAST(left_entity_id, right_entity_id)) STORED,
            high_entity_id uuid GENERATED ALWAYS AS (GREATEST(left_entity_id, right_entity_id)) STORED,
            entity_kind varchar(16) NOT NULL,
            mention_id uuid,
            work_item_id uuid,
            reason varchar(10) NOT NULL,
            match_probability numeric(6,5) NOT NULL,
            match_weight numeric(9,3) NOT NULL,
            comparison jsonb NOT NULL DEFAULT '{{}}'::jsonb,
            conflict_kinds text[] NOT NULL DEFAULT '{{}}',
            status varchar(10) NOT NULL DEFAULT 'OPEN',
            resolved_at timestamptz,
            resolved_by_user_id uuid REFERENCES users (id) ON DELETE SET NULL,
            created_at {TS},
            CONSTRAINT ck_entity_merge_candidates_distinct CHECK (left_entity_id <> right_entity_id),
            CONSTRAINT ck_entity_merge_candidates_kind CHECK (entity_kind IN ({_in(ENTITY_KINDS)})),
            CONSTRAINT ck_entity_merge_candidates_reason CHECK (reason IN ({_in(CANDIDATE_REASONS)})),
            CONSTRAINT ck_entity_merge_candidates_status CHECK (status IN ({_in(CANDIDATE_STATUSES)})),
            CONSTRAINT ck_entity_merge_candidates_resolved CHECK ((status = 'OPEN') = (resolved_at IS NULL)),
            CONSTRAINT ck_entity_merge_candidates_conflict_named CHECK (reason <> 'CONFLICT' OR cardinality(conflict_kinds) > 0),
            CONSTRAINT ck_entity_merge_candidates_probability CHECK (match_probability >= 0 AND match_probability <= 1),
            CONSTRAINT fk_entity_merge_candidates_workspace_org FOREIGN KEY (workspace_id, organization_id)
                REFERENCES workspaces (id, organization_id) ON DELETE CASCADE,
            CONSTRAINT fk_entity_merge_candidates_left FOREIGN KEY (left_entity_id, workspace_id)
                REFERENCES entities (id, workspace_id) ON DELETE CASCADE,
            CONSTRAINT fk_entity_merge_candidates_right FOREIGN KEY (right_entity_id, workspace_id)
                REFERENCES entities (id, workspace_id) ON DELETE CASCADE,
            CONSTRAINT fk_entity_merge_candidates_work_item FOREIGN KEY (work_item_id, workspace_id)
                REFERENCES work_items (id, workspace_id) ON DELETE SET NULL (work_item_id)
        )
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_entity_merge_candidates_open_pair ON entity_merge_candidates "
        "(workspace_id, low_entity_id, high_entity_id) WHERE status = 'OPEN'"
    )
    op.execute("CREATE INDEX ix_entity_merge_candidates_pair ON entity_merge_candidates (low_entity_id, high_entity_id, status)")

    # -- the review hub learns MERGE ------------------------------------------
    op.execute("ALTER TABLE review_assignments DROP CONSTRAINT ck_review_assignments_kind_known")
    op.execute(
        "ALTER TABLE review_assignments ADD CONSTRAINT ck_review_assignments_kind_known "
        f"CHECK (kind IN ({_in(REVIEW_KINDS)}))"
    )
    op.execute("DROP VIEW IF EXISTS review_queue_items")
    op.execute(review_queue_view_v3())

    _annotate_presets(add=True)


def downgrade() -> None:
    _annotate_presets(add=False)
    step2a = _step2a()
    op.execute("DROP VIEW IF EXISTS review_queue_items")
    op.execute(step2a.REVIEW_QUEUE_VIEW_V2)
    op.execute("DELETE FROM review_assignments WHERE kind = 'MERGE'")
    op.execute("ALTER TABLE review_assignments DROP CONSTRAINT ck_review_assignments_kind_known")
    op.execute(
        "ALTER TABLE review_assignments ADD CONSTRAINT ck_review_assignments_kind_known "
        f"CHECK (kind IN ({_in(REVIEW_KINDS_BEFORE)}))"
    )
    for table in TABLES_IN_DROP_ORDER:
        op.execute(f"DROP TABLE IF EXISTS {table}")

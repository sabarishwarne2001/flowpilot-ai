"""ARCH-34 Step 1 — document fingerprints, anomaly findings, suppressions (EXPAND)

Revision ID: arch34_step1_radar
Revises: arch33_step1_assertions
Create Date: 2026-09-15

PURE EXPAND
===========

Three new tables. Nothing existing is altered, narrowed or dropped, so every
row and every code path that predates ARCH-34 is untouched and the migration
is safe to run before the code that reads it ships. `downgrade()` drops the
three tables, which is honest: there is no data in them that predates this
phase, and nothing else references them.

THIRD-PARTY LICENSES INTRODUCED BY THIS PHASE
=============================================

None. Recorded explicitly, because a migration header is the one document in a
repository nobody deletes, and "did ARCH-34 add a dependency" is a question
that gets asked during diligence.

MinHash is hand-rolled over `hashlib` in `app/services/radar/fingerprint.py`.
`datasketch` is MIT and would have been acceptable; it is not used, for the
reasons recorded in that module. The embedding half of the radar reuses the
vectors ARCH-11 already computed — no new model, no new supplier, no new
recurring cost. No AGPL anywhere.

THE CONSTRAINT THAT CARRIES THE PHASE
=====================================

`ck_af_pairwise_has_counterpart` and `ck_af_evidence_present`, together.

    CHECK (layer IN ('PRICE_SURGE') OR counterpart_work_item_id IS NOT NULL)
    CHECK (jsonb_array_length(evidence) > 0)

A duplicate finding that cannot name the document it duplicates, or that
carries no evidence, is an accusation. This product shows accusations to a
person who then has to decide whether to hold a payment, and a row that says
"98% match" with an empty evidence array gives them nothing to check except
their own trust in the vendor.

The service layer mirrors both rules so the refusal arrives with an
explanation, but the database is the authority, because the service's version
survives only as long as nobody reorders the statements around it.
`verify_arch34.py --db` drives both INSERTs inside a rolled-back transaction
and requires Postgres to refuse them.

CANONICAL PAIR ORDERING IS DONE BY THE DATABASE, NOT BY THE APPLICATION
======================================================================

`(A, B)` and `(B, A)` must not both be storable. The obvious implementation
sorts the pair in Python before the insert, and it is the wrong one: it works
until the day a second writer — a backfill script, a reprocess job, a future
phase — inserts without sorting, and then the same duplicate sits in the queue
twice with the subject and counterpart swapped and no constraint anywhere
notices.

So the ordering is computed by Postgres, in two STORED generated columns:

    pair_lo uuid GENERATED ALWAYS AS (LEAST(subject, counterpart)) STORED
    pair_hi uuid GENERATED ALWAYS AS (GREATEST(subject, counterpart)) STORED

with a unique index on `(workspace_id, layer, pair_lo, pair_hi)`. No writer
can get it wrong, because no writer computes it.

`subject_work_item_id` KEEPS its meaning — the document that triggered the
sweep, which is the newer one, which is the one §5.6's headline leads with
("Invoice #982 matches invoice #411 from 2 months ago"). Forcing the SUBJECT
column itself into sorted order would have satisfied the same uniqueness
requirement and destroyed that, leaving the console to guess which of the two
documents a person just uploaded.

WHY `dedupe_key` SURVIVES ALONGSIDE `input_digest`
==================================================

They answer different questions and collapsing them loses one of the answers.

  * `dedupe_key` is finding IDENTITY: which series a PRICE_SURGE belongs to,
    hashed over `(layer, subject, vendor_key, sku)`. The pairwise layers get
    their identity from the generated columns above; price surges have no
    counterpart and therefore no pair, and a unique index containing a NULL
    would not collide with itself — Postgres treats NULLs as distinct, so
    every nightly run would insert the same surge again.

  * `input_digest` is RECOMPUTE NECESSITY: engine version, thresholds and the
    inputs that were compared. It is what makes the sweep idempotent — a
    re-run whose digest is unchanged writes nothing and, per §5.7's reasoning
    about incentives, emits no usage event.

A row can legitimately keep its `dedupe_key` while its `input_digest` changes
(the same series, re-swept under a new engine version). Merging them would
mean a version bump silently created duplicate findings.

WHY THE VOCABULARY LISTS ARE DECLARED HERE AND ASSERTED, NOT IMPORTED
=====================================================================

The tuples below duplicate `app/services/radar/vocabulary.py` on purpose. A
migration that imports application code is a migration that stops running the
day that import path moves, and the whole point of a migration is that it can
be replayed years later against a tree that has changed underneath it.

So they are copied, and `verify_arch34.py` asserts the copies are equal to the
originals. A fifth layer added in one place and not the other fails a gate
rather than a production insert.

`bigint[]` RATHER THAN §5.4's `integer[]`
=========================================

This is the one line of the roadmap this migration does not implement
literally, and the reason is arithmetic rather than preference.

A MinHash value is a 32-bit UNSIGNED quantity: the standard construction masks
the permuted hash with `(1 << 32) - 1`, so values run to 4,294,967,295.
Postgres `integer` is SIGNED four bytes and stops at 2,147,483,647. Roughly
half of every signature would overflow, and it would overflow at INSERT time
inside the fingerprint job rather than at review time.

The alternatives were to store values offset by 2^31 (correct, and it puts a
representation trick between the stored bytes and the algorithm forever) or to
narrow the hash to 31 bits (correct, and it degrades the estimator to buy back
512 bytes a row). `bigint[]` costs 1 KB per document for a table with one row
per work item. It is not close.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "arch34_step1_radar"
down_revision = "arch33_step1_assertions"
branch_labels = None
depends_on = None


# --- copies of app/services/radar/vocabulary.py, gated for equality ---

KINDS: tuple[str, ...] = (
    "DUPLICATE_DOCUMENT",
    "PRICE_SURGE",
    "CONTRACT_DRIFT",
)

LAYERS: tuple[str, ...] = (
    "L0",
    "L1",
    "L2",
    "L3",
    "PRICE_SURGE",
    "CONTRACT_DRIFT",
)

SEVERITIES: tuple[str, ...] = ("LOW", "MEDIUM", "HIGH")

STATUSES: tuple[str, ...] = ("OPEN", "CONFIRMED", "DISMISSED")

#: Layers whose finding MUST name a counterpart work item. The complement —
#: the layers that may not have one — is what the CHECK is written from.
UNPAIRED_LAYERS: tuple[str, ...] = ("PRICE_SURGE",)

#: The layer -> kind map, as a SQL expression. A row cannot claim
#: `kind='PRICE_SURGE', layer='L2'`.
DUPLICATE_LAYERS: tuple[str, ...] = ("L0", "L1", "L2", "L3")

MINHASH_PERMUTATIONS: int = 128

#: Copied from `arch11_step2_chunks_expand`, and asserted by
#: `verify_arch34.py` to equal `app.core.embeddings.active_dimension()` and
#: the declared width of `document_chunks.embedding`. A fingerprint whose mean
#: embedding is a different width than the chunks it was averaged from is a
#: cosine that can never be computed, discovered at query time.
EMBEDDING_DIMENSION: int = 384

#: pgvector index parameters, matching `ix_document_chunks_embedding_hnsw`.
HNSW_M: int = 16
HNSW_EF_CONSTRUCTION: int = 64


def _quoted(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def upgrade() -> None:
    # -----------------------------------------------------------------
    # 1. document_fingerprints
    # -----------------------------------------------------------------
    #
    # One row per work item, and the PRIMARY KEY says so. A work item cannot
    # have two fingerprints: re-running the fingerprint job after a re-OCR
    # must UPDATE, because two fingerprints for one document would both be
    # compared against every candidate and the radar would report each
    # document as a duplicate of itself under a different id.
    op.create_table(
        "document_fingerprints",
        sa.Column(
            "work_item_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("work_items.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        # ARCH-02. Both, always. `organization_id` is RESTRICT because an
        # organization with fingerprints is an organization with documents;
        # `workspace_id` is CASCADE because a deleted workspace's documents
        # are gone and their fingerprints are meaningless.
        sa.Column(
            "organization_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "workspace_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # L0's evidence. Copied from `uploaded_files.checksum_sha256` rather
        # than recomputed: re-hashing means re-reading the object out of
        # storage, and ARCH-06 already did it once at upload.
        sa.Column("content_sha256", sa.CHAR(64), nullable=False),
        # L1's evidence. Nullable — plenty of documents yield no vendor and no
        # number, and L1 declines rather than matching them on emptiness.
        sa.Column("vendor_key", sa.String(255), nullable=True),
        sa.Column("document_number", sa.String(128), nullable=True),
        # L2. See the module docstring for why this is bigint[].
        sa.Column(
            "minhash",
            postgresql.ARRAY(sa.BigInteger()),
            nullable=False,
        ),
        # Zero means the document produced no shingles. Load-bearing: two
        # empty signatures are identical, and without this column L2 cannot
        # tell "no text" from "no overlap".
        sa.Column("shingle_count", sa.Integer(), nullable=False),
        # L3's `embedding vector(384)` is NOT declared here. SQLAlchemy core
        # has no pgvector type, and `pgvector.sqlalchemy` is an application
        # import a migration must not depend on for the reasons in the
        # docstring. It is added immediately below by raw DDL, which is
        # replayable forever.
        sa.Column("embedding_model", sa.String(128), nullable=False),
        sa.Column("line_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("page_count", sa.Integer(), nullable=True),
        # L2's corroborating guards. Nullable, and `l2_minhash.py` treats
        # absence as "not measured" rather than "different" — see that module
        # for why the distinction decides whether recall depends on extraction
        # quality.
        sa.Column("document_date", sa.Date(), nullable=True),
        sa.Column("total_micros", sa.BigInteger(), nullable=True),
        sa.Column("currency", sa.String(3), nullable=True),
        sa.Column("engine_version", sa.String(32), nullable=False),
        sa.Column(
            "computed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            f"array_length(minhash, 1) = {MINHASH_PERMUTATIONS}",
            name="ck_df_minhash_length",
        ),
        sa.CheckConstraint(
            "shingle_count >= 0", name="ck_df_shingle_count_non_negative"
        ),
        sa.CheckConstraint("line_count >= 0", name="ck_df_line_count_non_negative"),
        sa.CheckConstraint(
            "page_count IS NULL OR page_count > 0", name="ck_df_page_count_positive"
        ),
        sa.CheckConstraint(
            "content_sha256 ~ '^[0-9a-f]{64}$'", name="ck_df_sha256_lowercase_hex"
        ),
        # An empty string is not an absent vendor. Nullable columns that also
        # accept '' have two representations of "unknown", and L1 compares for
        # equality — two documents with '' would match.
        sa.CheckConstraint(
            "vendor_key IS NULL OR btrim(vendor_key) <> ''",
            name="ck_df_vendor_key_present_or_null",
        ),
        sa.CheckConstraint(
            "document_number IS NULL OR btrim(document_number) <> ''",
            name="ck_df_document_number_present_or_null",
        ),
        sa.CheckConstraint(
            "currency IS NULL OR currency ~ '^[A-Z]{3}$'",
            name="ck_df_currency_iso",
        ),
    )

    # The vector column, declared for real, on a table that was created empty
    # one statement ago. `ADD COLUMN ... NOT NULL` with no default is legal
    # precisely because there are no rows to default; the alternative — adding
    # it nullable and narrowing later — would leave a window in which a
    # fingerprint with no embedding is storable, and L3 would compare against
    # a NULL that pgvector reports as "no match" rather than as an error.
    op.execute(
        f"ALTER TABLE document_fingerprints "
        f"ADD COLUMN embedding vector({EMBEDDING_DIMENSION}) NOT NULL"
    )

    # L0 and L1 are index lookups per §5.3, and these are the indexes that
    # make them so. Workspace-leading because every radar query is
    # tenant-scoped and ARCH-02 forbids a cross-workspace comparison.
    op.create_index(
        "ix_df_workspace_sha",
        "document_fingerprints",
        ["workspace_id", "content_sha256"],
    )
    op.create_index(
        "ix_df_workspace_vendor_number",
        "document_fingerprints",
        ["workspace_id", "vendor_key", "document_number"],
        postgresql_where=sa.text(
            "vendor_key IS NOT NULL AND document_number IS NOT NULL"
        ),
    )
    op.create_index(
        "ix_df_workspace_vendor_date",
        "document_fingerprints",
        ["workspace_id", "vendor_key", "document_date"],
    )
    # L3's candidate generation: pgvector HNSW top-k, as §5.3 specifies.
    # `vector_cosine_ops` and not `vector_l2_ops`: the stored embedding is
    # L2-normalised, so the two orderings agree, and declaring cosine keeps
    # the index honest about what the query means if normalisation ever moves.
    op.execute(
        f"CREATE INDEX ix_df_embedding_hnsw ON document_fingerprints "
        f"USING hnsw (embedding vector_cosine_ops) "
        f"WITH (m = {HNSW_M}, ef_construction = {HNSW_EF_CONSTRUCTION})"
    )

    # -----------------------------------------------------------------
    # 2. anomaly_findings
    # -----------------------------------------------------------------
    op.create_table(
        "anomaly_findings",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "organization_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "workspace_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # kind AND layer. kind is what the console groups by and what ARCH-13
        # filters on; layer is what tells the reader how much to trust it.
        # Layer cannot be derived from kind — DUPLICATE_DOCUMENT covers four
        # very different strengths of claim — which is why both columns exist.
        sa.Column("kind", sa.String(24), nullable=False),
        sa.Column("layer", sa.String(16), nullable=False),
        sa.Column("severity", sa.String(8), nullable=False),
        sa.Column(
            "subject_work_item_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("work_items.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # Nullable, because a price surge has no counterpart document. The
        # CHECK below is what stops that nullability from becoming a duplicate
        # finding with nothing to point at.
        sa.Column(
            "counterpart_work_item_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("work_items.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("score", sa.Numeric(6, 5), nullable=False),
        # Written from the metrics by a template, never generated. §5.6: the
        # sentence on screen is exactly what the numbers say.
        sa.Column("headline", sa.Text(), nullable=False),
        sa.Column(
            "metrics",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "evidence",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "status",
            sa.String(16),
            nullable=False,
            server_default=sa.text("'OPEN'"),
        ),
        sa.Column(
            "resolved_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolution_note", sa.Text(), nullable=True),
        # Finding identity. See the header for why this is not the same thing
        # as `input_digest` and why merging them would make a version bump
        # create duplicate findings.
        sa.Column("dedupe_key", sa.CHAR(64), nullable=False),
        # Recompute necessity. The sweep's idempotency key.
        sa.Column("input_digest", sa.CHAR(64), nullable=False),
        sa.Column("engine_version", sa.String(32), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        # --- the closed vocabularies ---
        sa.CheckConstraint(f"kind IN ({_quoted(KINDS)})", name="ck_af_kind_known"),
        sa.CheckConstraint(f"layer IN ({_quoted(LAYERS)})", name="ck_af_layer_known"),
        sa.CheckConstraint(
            f"severity IN ({_quoted(SEVERITIES)})", name="ck_af_severity_known"
        ),
        sa.CheckConstraint(
            f"status IN ({_quoted(STATUSES)})", name="ck_af_status_known"
        ),
        # kind and layer must agree. Written as an explicit biconditional
        # rather than a lookup so the rule is readable in `\d anomaly_findings`
        # by somebody who has never seen vocabulary.py.
        sa.CheckConstraint(
            f"(layer IN ({_quoted(DUPLICATE_LAYERS)}) AND kind = "
            "'DUPLICATE_DOCUMENT') OR (layer = 'PRICE_SURGE' AND kind = "
            "'PRICE_SURGE') OR (layer = 'CONTRACT_DRIFT' AND kind = "
            "'CONTRACT_DRIFT')",
            name="ck_af_kind_matches_layer",
        ),
        # --- THE CONSTRAINT THAT CARRIES THE PHASE ---
        sa.CheckConstraint(
            f"layer IN ({_quoted(UNPAIRED_LAYERS)}) "
            "OR counterpart_work_item_id IS NOT NULL",
            name="ck_af_pairwise_has_counterpart",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(evidence) = 'array' AND jsonb_array_length(evidence) > 0",
            name="ck_af_evidence_present",
        ),
        # A document is not a duplicate of itself. The generated columns below
        # would happily accept it — LEAST(A, A) = GREATEST(A, A) — and the
        # unique index would then allow exactly one self-pair per layer, which
        # is one more than zero.
        sa.CheckConstraint(
            "counterpart_work_item_id IS NULL "
            "OR counterpart_work_item_id <> subject_work_item_id",
            name="ck_af_counterpart_is_not_subject",
        ),
        sa.CheckConstraint(
            "score >= 0 AND score <= 1", name="ck_af_score_unit_interval"
        ),
        sa.CheckConstraint("btrim(headline) <> ''", name="ck_af_headline_present"),
        # A resolved finding names who resolved it and when. Without this a
        # DISMISSED row is an anonymous decision, and "who said this duplicate
        # was fine" is the first question asked after one is paid twice.
        sa.CheckConstraint(
            "status = 'OPEN' "
            "OR (resolved_by_user_id IS NOT NULL AND resolved_at IS NOT NULL)",
            name="ck_af_resolution_has_actor",
        ),
        # And a DISMISSED one names why. CONFIRMED does not need a reason —
        # agreeing with the engine adds no information — but disagreeing with
        # it does, and that reason is what `anomaly_suppressions` carries
        # forward so the same pair stops resurfacing.
        sa.CheckConstraint(
            "status <> 'DISMISSED' OR btrim(coalesce(resolution_note, '')) <> ''",
            name="ck_af_dismissal_has_reason",
        ),
        sa.CheckConstraint(
            "dedupe_key ~ '^[0-9a-f]{64}$'", name="ck_af_dedupe_key_hex"
        ),
        sa.CheckConstraint(
            "input_digest ~ '^[0-9a-f]{64}$'", name="ck_af_input_digest_hex"
        ),
    )

    # The canonical pair ordering, computed by Postgres so that no writer can
    # get it wrong. STORED rather than VIRTUAL because a unique index needs
    # the value materialised.
    op.execute(
        "ALTER TABLE anomaly_findings "
        "ADD COLUMN pair_lo uuid GENERATED ALWAYS AS "
        "(LEAST(subject_work_item_id, counterpart_work_item_id)) STORED"
    )
    op.execute(
        "ALTER TABLE anomaly_findings "
        "ADD COLUMN pair_hi uuid GENERATED ALWAYS AS "
        "(GREATEST(subject_work_item_id, counterpart_work_item_id)) STORED"
    )

    # (A, B) and (B, A) collide here, at the same layer, in the same
    # workspace. Partial, because a price surge has no pair and NULLs in a
    # unique index do not collide with each other.
    op.create_index(
        "uq_af_pair_layer",
        "anomaly_findings",
        ["workspace_id", "layer", "pair_lo", "pair_hi"],
        unique=True,
        postgresql_where=sa.text("counterpart_work_item_id IS NOT NULL"),
    )
    # And the unpaired kinds get their identity from `dedupe_key`, which the
    # service computes over (layer, subject, vendor_key, sku).
    op.create_index(
        "uq_af_dedupe_unpaired",
        "anomaly_findings",
        ["workspace_id", "dedupe_key"],
        unique=True,
        postgresql_where=sa.text("counterpart_work_item_id IS NULL"),
    )
    # §5.6's feed: workspace, then the filters, then newest first.
    op.create_index(
        "ix_af_feed",
        "anomaly_findings",
        ["workspace_id", "status", "severity", sa.text("created_at DESC")],
    )
    op.create_index(
        "ix_af_kind_feed",
        "anomaly_findings",
        ["workspace_id", "kind", "status", sa.text("created_at DESC")],
    )
    # "What does the radar say about the document I am looking at" — from both
    # directions, because the document a person opens may be either side of
    # the pair.
    op.create_index(
        "ix_af_subject", "anomaly_findings", ["subject_work_item_id", "status"]
    )
    op.create_index(
        "ix_af_counterpart",
        "anomaly_findings",
        ["counterpart_work_item_id", "status"],
        postgresql_where=sa.text("counterpart_work_item_id IS NOT NULL"),
    )
    # The sweep's idempotency probe.
    op.create_index(
        "ix_af_input_digest", "anomaly_findings", ["workspace_id", "input_digest"]
    )

    # -----------------------------------------------------------------
    # 3. anomaly_suppressions
    # -----------------------------------------------------------------
    #
    # "This is fine, and here is why." §5.4: dismissing a duplicate writes a
    # suppression for that PAIR, so a legitimately repeated monthly invoice
    # with the same amount stops resurfacing once a person has said so.
    #
    # The finding is never deleted. A dismissed finding is a record that the
    # engine raised something and a named person judged it; deleting it would
    # erase both halves, and the audit question after a double payment is
    # exactly "did the system see this, and what did we do".
    op.create_table(
        "anomaly_suppressions",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "organization_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "workspace_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(24), nullable=False),
        # The layer the suppression was granted AT, so that suppressing a
        # weak L3 guess does not also suppress a future byte-identical L0
        # match on the same pair. A reviewer who says "these two similar
        # quotes are not duplicates" has not said "and never tell me if
        # somebody uploads the exact same file twice".
        sa.Column("layer", sa.String(16), nullable=False),
        sa.Column(
            "item_a_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("work_items.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "item_b_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("work_items.id", ondelete="CASCADE"),
            nullable=True,
        ),
        # For PRICE_SURGE, which has no pair: the series that was suppressed.
        sa.Column("vendor_key", sa.String(255), nullable=True),
        sa.Column("sku", sa.String(128), nullable=True),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column(
            "created_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        # Suppressions expire by default. A permanent one is a permanent blind
        # spot, and the reviewer granting it at 17:40 on a Friday is not
        # thinking about next year. NULL means "never expires" and is
        # deliberately expressible, because some suppressions genuinely are
        # structural.
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(f"kind IN ({_quoted(KINDS)})", name="ck_as_kind_known"),
        sa.CheckConstraint(f"layer IN ({_quoted(LAYERS)})", name="ck_as_layer_known"),
        # A reason is mandatory and cannot be whitespace. "Not an anomaly" with
        # no reason is indistinguishable from a misclick six months later, and
        # the next reviewer inherits a silence.
        sa.CheckConstraint("btrim(reason) <> ''", name="ck_as_reason_present"),
        sa.CheckConstraint(
            "item_b_id IS NULL OR item_b_id <> item_a_id",
            name="ck_as_items_distinct",
        ),
        # A pairwise suppression names both items; a surge suppression names
        # the series. Neither may be half-specified.
        sa.CheckConstraint(
            f"(layer IN ({_quoted(UNPAIRED_LAYERS)}) AND item_b_id IS NULL "
            "AND vendor_key IS NOT NULL) "
            f"OR (layer NOT IN ({_quoted(UNPAIRED_LAYERS)}) "
            "AND item_b_id IS NOT NULL)",
            name="ck_as_scope_matches_layer",
        ),
    )

    # The suppression lookup the sweep does before writing a finding. Ordered
    # pair, same reasoning as the findings index — except computed here in the
    # index expression rather than in generated columns, because a suppression
    # row has no other use for the values.
    op.execute(
        "CREATE UNIQUE INDEX uq_as_pair_layer ON anomaly_suppressions "
        "(workspace_id, layer, LEAST(item_a_id, item_b_id), "
        "GREATEST(item_a_id, item_b_id)) WHERE item_b_id IS NOT NULL"
    )
    op.create_index(
        "uq_as_series",
        "anomaly_suppressions",
        ["workspace_id", "layer", "vendor_key", "sku"],
        unique=True,
        postgresql_where=sa.text("item_b_id IS NULL"),
    )
    op.create_index(
        "ix_as_workspace_kind", "anomaly_suppressions", ["workspace_id", "kind"]
    )


def downgrade() -> None:
    # Reverse order: findings and suppressions reference work items, not each
    # other, so the order between them is free, but dropping fingerprints last
    # keeps the read of this function matching the read of upgrade().
    op.drop_index("ix_as_workspace_kind", table_name="anomaly_suppressions")
    op.drop_index("uq_as_series", table_name="anomaly_suppressions")
    op.execute("DROP INDEX IF EXISTS uq_as_pair_layer")
    op.drop_table("anomaly_suppressions")

    op.drop_index("ix_af_input_digest", table_name="anomaly_findings")
    op.drop_index("ix_af_counterpart", table_name="anomaly_findings")
    op.drop_index("ix_af_subject", table_name="anomaly_findings")
    op.drop_index("ix_af_kind_feed", table_name="anomaly_findings")
    op.drop_index("ix_af_feed", table_name="anomaly_findings")
    op.drop_index("uq_af_dedupe_unpaired", table_name="anomaly_findings")
    op.drop_index("uq_af_pair_layer", table_name="anomaly_findings")
    op.drop_table("anomaly_findings")

    op.execute("DROP INDEX IF EXISTS ix_df_embedding_hnsw")
    op.drop_index("ix_df_workspace_vendor_date", table_name="document_fingerprints")
    op.drop_index("ix_df_workspace_vendor_number", table_name="document_fingerprints")
    op.drop_index("ix_df_workspace_sha", table_name="document_fingerprints")
    op.drop_table("document_fingerprints")

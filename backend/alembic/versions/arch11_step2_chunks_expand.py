"""ARCH-11 Step 2 — pgvector + document_chunks (EXPAND)

Revision ID: arch11_step2_chunks_expand
Revises: arch10_step8_jobs_type_index
Create Date: 2026-08-18

Purely additive. Nothing reads `document_chunks` after this revision — Step 3
writes to it, Step 4 backfills it, Step 6 queries it. Deploying this migration
on its own changes no behaviour, which is the property that makes it safe to
land ahead of the code.

## Why raw SQL rather than `op.create_table`

`op.create_table(..., postgresql_partition_by=...)` works and emits correct
DDL. The partitions do not: sixteen `CREATE TABLE ... PARTITION OF` statements
have no Alembic operation, so half of this migration would be `op.execute`
anyway. One consistent style is easier to read than two, and it makes the
generated-column expression and the HNSW `WITH` options visible verbatim
instead of assembled from keyword arguments.

`alembic revision --autogenerate` must still come back **empty** after this
runs, which is why `app/models/document_chunk.py` declares every index and
constraint with matching names. Autogenerate does not compare partitioning, so
the partition clause is invisible to it — that is fine, and it is why
`scripts/verify_arch11_step2.py` checks partitioning directly rather than
trusting an empty diff.

## Order of operations, and why it is this order

1. Extension first — every later statement depends on the `vector` type.
2. Parent table, then partitions, then indexes on the **parent**. Creating
   indexes on a partitioned parent propagates them to every existing partition
   and to every partition created later. Doing it in this order means one
   statement per index instead of sixteen.
3. `maintenance_work_mem` is raised for the index build. It does nothing here —
   the table is empty — and it is set anyway so that the same statement is
   correct when Step 4's backfill rebuilds these indexes over a populated
   table, where an HNSW build that spills to disk is 10-50x slower (R35).
"""

from __future__ import annotations

from alembic import op

revision = "arch11_step2_chunks_expand"
down_revision = "arch10_step8_jobs_type_index"
branch_labels = None
depends_on = None

#: R36. Changing this later is a table rewrite, not a migration. Sized for
#: three years of tenant growth, not for today's count: at sixteen partitions
#: and a few thousand workspaces you are back to hundreds of tenants per index,
#: which is the ratio that produced the under-return in §4.
PARTITIONS = 16

TSVECTOR_CONFIG = "english"
EMBEDDING_DIMENSION = 384

MINIMUM_PGVECTOR = (0, 8)


def _assert_pgvector_available() -> None:
    """Fail with a readable message rather than a type error 200 lines later."""
    available = op.get_bind().exec_driver_sql(
        "SELECT default_version FROM pg_available_extensions WHERE name = 'vector'"
    ).scalar()
    if available is None:
        raise RuntimeError(
            "The `vector` extension is not available on this server. ARCH-11 "
            "cannot proceed: install pgvector (>= 0.8) or point at the "
            "pgvector/pgvector:pg16 image. This is pre-flight check V.1.1 and "
            "it is blocking for a reason."
        )
    parts = tuple(int(p) for p in str(available).split(".")[:2])
    if parts < MINIMUM_PGVECTOR:
        raise RuntimeError(
            f"pgvector {available} is below {MINIMUM_PGVECTOR[0]}."
            f"{MINIMUM_PGVECTOR[1]}. `hnsw.iterative_scan` does not exist "
            "before 0.8, and without it a tenant-filtered vector query "
            "silently returns fewer rows than asked for — see ARCH-11 §4. "
            "Partitioning alone reduces the ratio; it does not remove it."
        )


def upgrade() -> None:
    _assert_pgvector_available()

    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    # Step 5 needs these; they are cheap, they are already available per V.1.2,
    # and creating them here means Step 5 is a query change with no migration.
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.execute("CREATE EXTENSION IF NOT EXISTS unaccent")

    op.execute(
        f"""
        CREATE TABLE document_chunks (
            id                UUID        NOT NULL,

            -- Tenancy. workspace_id is the partition key and leads the primary
            -- key, so the implicit PK index also serves every scoped lookup.
            workspace_id      UUID        NOT NULL,
            -- Denormalised from workspaces: every metering and quota query
            -- needs it, and a join on the retrieval hot path to recover a
            -- tenant you already know is paid on every question.
            organization_id   UUID        NOT NULL,

            -- Provenance. Populated by Step 3; nullable so Step 2 does not
            -- block on it. Recording it later means re-chunking and
            -- re-embedding every document any customer has uploaded.
            work_item_id      UUID        NOT NULL,
            uploaded_file_id  UUID        NULL,
            chunk_index       INTEGER     NOT NULL,
            page_number       INTEGER     NULL,
            page_start_char   INTEGER     NULL,
            page_end_char     INTEGER     NULL,
            bbox              JSONB       NULL,

            content           TEXT        NOT NULL,
            -- Generated, not application-maintained. The two-argument
            -- to_tsvector is IMMUTABLE; the one-argument form reads a GUC and
            -- PostgreSQL will refuse it in a generated column.
            content_tsv       TSVECTOR
                GENERATED ALWAYS AS (to_tsvector('{TSVECTOR_CONFIG}', content)) STORED,
            token_count       INTEGER     NOT NULL,

            embedding         VECTOR({EMBEDDING_DIMENSION}) NOT NULL,
            embedding_model   VARCHAR(100) NOT NULL,

            created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),

            CONSTRAINT pk_document_chunks PRIMARY KEY (workspace_id, id),

            -- The single largest win of this migration, and it is not about
            -- performance: after this, a chunk cannot outlive its document.
            CONSTRAINT fk_document_chunks_work_item_id_work_items
                FOREIGN KEY (work_item_id) REFERENCES work_items (id)
                ON DELETE CASCADE,
            CONSTRAINT fk_document_chunks_workspace_id_workspaces
                FOREIGN KEY (workspace_id) REFERENCES workspaces (id)
                ON DELETE CASCADE,
            CONSTRAINT fk_document_chunks_organization_id_organizations
                FOREIGN KEY (organization_id) REFERENCES organizations (id)
                ON DELETE CASCADE,
            CONSTRAINT fk_document_chunks_uploaded_file_id_uploaded_files
                FOREIGN KEY (uploaded_file_id) REFERENCES uploaded_files (id)
                ON DELETE SET NULL,

            CONSTRAINT ck_document_chunks_chunk_index_non_negative
                CHECK (chunk_index >= 0),
            CONSTRAINT ck_document_chunks_token_count_positive
                CHECK (token_count > 0),
            CONSTRAINT ck_document_chunks_content_not_blank
                CHECK (length(content) > 0),
            CONSTRAINT ck_document_chunks_char_span_ordered
                CHECK (page_start_char IS NULL OR page_end_char IS NULL
                       OR page_end_char >= page_start_char),
            CONSTRAINT ck_document_chunks_bbox_is_object
                CHECK (bbox IS NULL OR jsonb_typeof(bbox) = 'object')
        ) PARTITION BY HASH (workspace_id)
        """
    )

    for remainder in range(PARTITIONS):
        op.execute(
            f"""
            CREATE TABLE document_chunks_p{remainder:02d}
                PARTITION OF document_chunks
                FOR VALUES WITH (MODULUS {PARTITIONS}, REMAINDER {remainder})
            """
        )

    # See the module docstring: no-op on an empty table, correct when Step 4
    # rebuilds these over a populated one.
    op.execute("SET LOCAL maintenance_work_mem = '512MB'")

    op.execute(
        """
        CREATE UNIQUE INDEX uq_document_chunks_workspace_work_item_chunk
            ON document_chunks (workspace_id, work_item_id, chunk_index)
        """
    )
    op.execute(
        """
        CREATE INDEX ix_document_chunks_work_item
            ON document_chunks (workspace_id, work_item_id)
        """
    )
    # Cosine, not L2: embeddings are L2-normalised at generation
    # (`normalize_embeddings=True`), so cosine and inner product rank
    # identically and cosine keeps `1 - distance` readable as a similarity.
    # HNSW, not IVFFlat: IVFFlat needs representative data to build its lists
    # and this table starts empty, which is the case IVFFlat handles worst.
    op.execute(
        """
        CREATE INDEX ix_document_chunks_embedding_hnsw
            ON document_chunks USING hnsw (embedding vector_cosine_ops)
            WITH (m = 16, ef_construction = 64)
        """
    )
    op.execute(
        """
        CREATE INDEX ix_document_chunks_content_tsv
            ON document_chunks USING gin (content_tsv)
        """
    )
    # Step 5's fuzzy arm: invoice numbers, part codes, names — the queries
    # where full-text stemming is the wrong tool.
    op.execute(
        """
        CREATE INDEX ix_document_chunks_content_trgm
            ON document_chunks USING gin (content gin_trgm_ops)
        """
    )

    op.execute(
        """
        COMMENT ON TABLE document_chunks IS
        'ARCH-11. Partitioned by HASH(workspace_id); the modulus is fixed at '
        'migration time and changing it is a table rewrite. Every read goes '
        'through app.db.chunk_scope.scoped_chunk_query.'
        """
    )


def downgrade() -> None:
    """Drops the table and its partitions. The extensions stay.

    Dropping `vector` would cascade to any other object depending on the type,
    and an extension is not the migration's to remove — it is closer to a
    server configuration than to schema. `pg_trgm` and `unaccent` are likewise
    left in place; both are harmless and both were available before this
    revision ran.
    """
    op.execute("DROP TABLE IF EXISTS document_chunks CASCADE")
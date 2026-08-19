"""ARCH-11 Step 2 — `document_chunks`, the substrate the phase exists to build.

Four properties this table has that the Chroma collection it replaces did not,
in descending order of how much they matter:

**1. A chunk cannot outlive its document.** `work_item_id` is a foreign key
with `ON DELETE CASCADE`. Chroma had no foreign key to PostgreSQL, so deleting
a workspace never deleted its vectors and a rolled-back enrich transaction left
vectors behind for a document that does not exist. V.2.3 counted 336 orphaned
collections; that number is a GDPR erasure question, not untidiness, and it is
recorded in the ARCH-18 notes for that reason.

**2. The tenancy predicate is in the SQL.** Not in a collection name, not in a
metadata filter, not in an application convention — in a `WHERE` clause the
database enforces, reached through exactly one helper (`app.db.chunk_scope`).

**3. Provenance survives chunking.** `page_number`, `page_start_char`,
`page_end_char` and `bbox` are what make "the AI said so" into "here is the
page, here is the box on it, here is the audit row proving what the model saw".
That is the named commercial moat, and ARCH-10 Step 6 already preserved the
per-block boxes into `work_items.extraction_metadata`. The join is recoverable
only at chunk time: `OCRPage.text` is the newline-join of its block texts, so
each block occupies a known character span in the page and a chunk that records
its own span can intersect them and carry the union box. Columns exist from
Step 2 even though Step 3 is what populates them, because adding them later
means re-embedding every document any customer has ever uploaded.

**4. Hash partitioning by `workspace_id`.** Not for size — for correctness. A
filtered HNSW query searches the index first and applies `WHERE` afterwards, so
a tenant holding a small share of a shared index gets a candidate set that is
almost entirely other tenants' rows, which are then filtered away, and the
query returns too few rows with no error. Measured at 100k vectors / 60
tenants: a 20-chunk tenant asked for 10 and got **0**, still 0 at
`ef_search=400`. Sixteen partitions turned that into 10. `hnsw.iterative_scan`
(pgvector ≥ 0.8, applied by the session hook in `app/db/session.py`) is the
other half; both are wanted, neither alone is sufficient at scale.

The consequence to accept: a partitioned table requires the partition key in
every unique constraint, so the primary key is `(workspace_id, id)` and not
`id`. Declared in that column order deliberately — the implicit PK index is
then also the index that serves every tenant-scoped lookup.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Optional

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    CheckConstraint,
    Computed,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    PrimaryKeyConstraint,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.config import settings
from app.core.embeddings import (
    active_dimension,
    active_model_name,
    assert_settings_are_coherent,
)
from app.db.base import Base

#: Fixed at migration time. `vector(n)` cannot be widened in place, and the
#: registry in app/core/embeddings.py is what keeps the configured model and
#: this literal from drifting apart silently.
assert_settings_are_coherent()
EMBEDDING_DIMENSION: int = active_dimension()

#: The text search configuration. Named here rather than left to
#: `default_text_search_config`, because a generated column's expression must be
#: IMMUTABLE and the one-argument `to_tsvector(text)` is not — it reads a GUC.
#: Changing this value is a table rewrite; it is not a runtime setting.
TSVECTOR_CONFIG = "english"


class DocumentChunk(Base):
    """One embedded passage of one document, owned by exactly one workspace."""

    __tablename__ = "document_chunks"

    __table_args__ = (
        # Partition key first. See the module docstring.
        PrimaryKeyConstraint("workspace_id", "id", name="pk_document_chunks"),
        ForeignKeyConstraint(
            ["work_item_id"],
            ["work_items.id"],
            ondelete="CASCADE",
            name="fk_document_chunks_work_item_id_work_items",
        ),
        ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            ondelete="CASCADE",
            name="fk_document_chunks_workspace_id_workspaces",
        ),
        ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            ondelete="CASCADE",
            name="fk_document_chunks_organization_id_organizations",
        ),
        ForeignKeyConstraint(
            ["uploaded_file_id"],
            ["uploaded_files.id"],
            ondelete="SET NULL",
            name="fk_document_chunks_uploaded_file_id_uploaded_files",
        ),
        CheckConstraint("chunk_index >= 0", name="chunk_index_non_negative"),
        CheckConstraint("token_count > 0", name="token_count_positive"),
        CheckConstraint("length(content) > 0", name="content_not_blank"),
        CheckConstraint(
            "page_start_char IS NULL OR page_end_char IS NULL "
            "OR page_end_char >= page_start_char",
            name="char_span_ordered",
        ),
        CheckConstraint(
            "bbox IS NULL OR jsonb_typeof(bbox) = 'object'",
            name="bbox_is_object",
        ),
        # A document's chunk sequence is unique within its workspace. The
        # partition key has to be in it, which here costs nothing because
        # every read is workspace-scoped anyway.
        Index(
            "uq_document_chunks_workspace_work_item_chunk",
            "workspace_id",
            "work_item_id",
            "chunk_index",
            unique=True,
        ),
        # Deletion, re-index and "show me this document's chunks".
        Index("ix_document_chunks_work_item", "workspace_id", "work_item_id"),
        # Dense retrieval. Cosine because embeddings are L2-normalised at
        # generation time (`normalize_embeddings=True`), which makes cosine and
        # inner product rank-equivalent; cosine is chosen because it keeps the
        # stored similarity readable as 1 - distance without knowing that.
        Index(
            "ix_document_chunks_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
            postgresql_with={"m": "16", "ef_construction": "64"},
        ),
        # Lexical retrieval (Step 5). Built now so Step 5 is a query change
        # rather than a migration on a populated table.
        Index(
            "ix_document_chunks_content_tsv",
            "content_tsv",
            postgresql_using="gin",
        ),
        {"postgresql_partition_by": "HASH (workspace_id)"},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), default=uuid.uuid4, nullable=False
    )

    # --- tenancy -----------------------------------------------------------
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False
    )
    #: Denormalised from `workspaces`. It duplicates a reachable fact and it is
    #: worth it: every metering and quota query needs the organization, and a
    #: join on the retrieval hot path to recover a tenant you already know is a
    #: cost paid on every question a customer ever asks.
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False
    )

    # --- provenance --------------------------------------------------------
    work_item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False
    )
    uploaded_file_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    page_number: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    #: Character offsets into `OCRPage.text` for the page above. Populated in
    #: Step 3; nullable here because Step 2 must not block on Step 3.
    page_start_char: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    page_end_char: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    #: Union of the OCR block boxes this chunk's span intersects.
    #: `{"page": int, "x0": float, "y0": float, "x1": float, "y1": float,
    #:   "blocks": [...]}` — normalised page coordinates, origin top-left.
    bbox: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONB, nullable=True)

    # --- content -----------------------------------------------------------
    content: Mapped[str] = mapped_column(Text, nullable=False)
    #: Generated, not application-maintained. An application-maintained tsvector
    #: is a column that is correct until the one write path that forgets it.
    content_tsv: Mapped[str] = mapped_column(
        TSVECTOR,
        Computed(f"to_tsvector('{TSVECTOR_CONFIG}', content)", persisted=True),
        nullable=False,
    )
    token_count: Mapped[int] = mapped_column(Integer, nullable=False)

    # --- vector ------------------------------------------------------------
    embedding: Mapped[Any] = mapped_column(
        Vector(EMBEDDING_DIMENSION), nullable=False
    )
    #: The canonical model name, per chunk. This is the §0.4 decision: the
    #: workspace-level setting is retired, and the model is recorded where a
    #: future incremental migration can act on it row by row.
    embedding_model: Mapped[str] = mapped_column(
        String(100), nullable=False, default=active_model_name
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"<DocumentChunk work_item={self.work_item_id} "
            f"index={self.chunk_index} page={self.page_number}>"
        )


#: Number of hash partitions created by the Step 2 migration. Changing it is a
#: table rewrite (R36); tests and verification scripts assert against this.
PARTITION_COUNT: int = int(getattr(settings, "DOCUMENT_CHUNK_PARTITIONS", 16))


def partition_name(modulus_remainder: int) -> str:
    return f"document_chunks_p{modulus_remainder:02d}"


__all__ = [
    "DocumentChunk",
    "EMBEDDING_DIMENSION",
    "PARTITION_COUNT",
    "TSVECTOR_CONFIG",
    "partition_name",
]
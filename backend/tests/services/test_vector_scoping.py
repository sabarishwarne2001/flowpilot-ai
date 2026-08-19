"""ARCH-11 Step 2 — the scoped helper, and the scan that keeps it the only door.

The behavioural leak matrix is Step 8's job (`tests/isolation/`). What is here
is narrower and runs on every commit: the helper always emits the predicate,
refuses to build a query without one, and no other module in `app/` reaches
`document_chunks` directly.

`test_no_unscoped_chunk_access_in_app` is the important one, and it is the one
that will annoy somebody in three months. That annoyance is the feature: the
day it fires is the day a retrieval path was written without a tenancy
predicate, and there is no other point at which that gets noticed, because the
query returns plausible rows either way.
"""

from __future__ import annotations

import re
import uuid
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Session

from app.db.chunk_scope import (
    VectorScopeError,
    count_chunks,
    delete_chunks_for_work_item,
    nearest_chunks,
    scoped_chunk_query,
)
from app.models.document_chunk import (
    EMBEDDING_DIMENSION,
    PARTITION_COUNT,
    DocumentChunk,
)

APP_ROOT = Path(__file__).resolve().parents[2] / "app"

#: Modules allowed to name the table or the model. Everything else must go
#: through `scoped_chunk_query`. Adding to this list is a deliberate act and
#: should come with a reason in the commit message.
SCOPE_ALLOW_LIST = {
    "app/models/document_chunk.py",
    "app/db/chunk_scope.py",
    "app/models/__init__.py",
    "app/core/embeddings.py",
    "app/services/document_models.py",
    "app/services/chunking_service.py",
    "app/services/embedding_service.py",
}

_RAW_REFERENCE = re.compile(r"\bdocument_chunks\b|\bDocumentChunk\b")


def _compiled(statement) -> str:
    return str(
        statement.compile(
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
        )
    )


# ===========================================================================
# The helper always scopes
# ===========================================================================


@pytest.mark.no_db
def test_query_carries_the_workspace_predicate():
    workspace_id = uuid.uuid4()
    sql = _compiled(scoped_chunk_query(None, workspace_id))
    assert "document_chunks.workspace_id" in sql
    assert str(workspace_id) in sql


@pytest.mark.no_db
def test_organization_predicate_is_additive():
    workspace_id, organization_id = uuid.uuid4(), uuid.uuid4()
    sql = _compiled(
        scoped_chunk_query(None, workspace_id, organization_id=organization_id)
    )
    assert "document_chunks.workspace_id" in sql
    assert "document_chunks.organization_id" in sql


@pytest.mark.no_db
@pytest.mark.parametrize("bad", [None, "", "not-a-uuid", 17])
def test_missing_or_malformed_workspace_is_refused(bad):
    with pytest.raises(VectorScopeError):
        scoped_chunk_query(None, bad)


@pytest.mark.no_db
def test_empty_work_item_filter_returns_nothing_not_everything():
    """A workspace whose documents are all still extracting has no searchable
    corpus. Dropping the filter here would widen the query to the workspace,
    which is the mistake that looks like a feature until it isn't."""
    sql = _compiled(scoped_chunk_query(None, uuid.uuid4(), work_item_ids=[]))
    assert "false" in sql.lower()


@pytest.mark.no_db
def test_work_item_filter_is_an_in_clause():
    ids = [uuid.uuid4(), uuid.uuid4()]
    sql = _compiled(scoped_chunk_query(None, uuid.uuid4(), work_item_ids=ids))
    assert "IN (" in sql
    for value in ids:
        assert str(value) in sql


# ===========================================================================
# The schema is what the plan said it would be
# ===========================================================================


@pytest.mark.no_db
def test_primary_key_leads_with_the_partition_key():
    """A partitioned table requires the partition key in every unique
    constraint. Declaring it first means the implicit PK index also serves
    tenant-scoped lookups."""
    pk = list(DocumentChunk.__table__.primary_key.columns)
    assert [column.name for column in pk] == ["workspace_id", "id"]


@pytest.mark.no_db
def test_table_declares_hash_partitioning():
    assert DocumentChunk.__table__.kwargs.get("postgresql_partition_by") == (
        "HASH (workspace_id)"
    )
    assert PARTITION_COUNT == 16


@pytest.mark.no_db
def test_work_item_foreign_key_cascades():
    """The single largest win of the migration: a chunk cannot outlive its
    document. Chroma had no foreign key, which is why V.2.3 found 336 orphaned
    collections."""
    cascades = {
        (list(fk.columns)[0].name, fk.ondelete)
        for fk in DocumentChunk.__table__.foreign_key_constraints
    }
    assert ("work_item_id", "CASCADE") in cascades
    assert ("workspace_id", "CASCADE") in cascades
    assert ("organization_id", "CASCADE") in cascades
    assert ("uploaded_file_id", "SET NULL") in cascades


@pytest.mark.no_db
def test_embedding_dimension_matches_the_configured_model():
    from app.core.embeddings import active_dimension

    assert EMBEDDING_DIMENSION == active_dimension() == 384


@pytest.mark.no_db
def test_hnsw_index_uses_cosine_ops():
    index = next(
        ix
        for ix in DocumentChunk.__table__.indexes
        if ix.name == "ix_document_chunks_embedding_hnsw"
    )
    assert index.dialect_options["postgresql"]["using"] == "hnsw"
    assert index.dialect_options["postgresql"]["ops"] == {
        "embedding": "vector_cosine_ops"
    }
    assert index.dialect_options["postgresql"]["with"] == {
        "m": "16",
        "ef_construction": "64",
    }


@pytest.mark.no_db
def test_content_tsv_is_generated_not_written():
    column = DocumentChunk.__table__.c.content_tsv
    assert column.computed is not None
    assert column.computed.persisted is True
    # The two-argument form is IMMUTABLE; the one-argument form reads a GUC and
    # PostgreSQL refuses it in a generated column.
    assert "to_tsvector('english', content)" in str(column.computed.sqltext)


@pytest.mark.no_db
def test_provenance_columns_exist_even_though_step_3_populates_them():
    columns = set(DocumentChunk.__table__.c.keys())
    assert {"page_number", "page_start_char", "page_end_char", "bbox"} <= columns


# ===========================================================================
# The static scan
# ===========================================================================


@pytest.mark.no_db
def test_no_unscoped_chunk_access_in_app():
    offenders: list[str] = []
    for path in APP_ROOT.rglob("*.py"):
        relative = path.relative_to(APP_ROOT.parent).as_posix()
        if relative in SCOPE_ALLOW_LIST:
            continue
        source = path.read_text(encoding="utf-8", errors="replace")
        for number, line in enumerate(source.splitlines(), start=1):
            stripped = line.strip()
            if stripped.startswith("#") or not _RAW_REFERENCE.search(line):
                continue
            # An import of the model is fine; a query built from it is not.
            if stripped.startswith(("from app.models.document_chunk import",
                                    "import app.models.document_chunk")):
                continue
            offenders.append(f"{relative}:{number}: {stripped}")

    assert not offenders, (
        "document_chunks is reachable outside app/db/chunk_scope.py:\n  "
        + "\n  ".join(offenders)
        + "\n\nEvery retrieval path needs the tenancy predicate in the SQL. "
        "Route it through scoped_chunk_query, or add the file to "
        "SCOPE_ALLOW_LIST with a reason."
    )


# ===========================================================================
# Against a real database
# ===========================================================================


@pytest.fixture()
def seeded(db_session: Session):
    """Two workspaces in different organizations, chunks in each."""
    from app.models.organization import Organization, OrganizationStatus
    from app.models.user import User
    from app.models.work_item import WorkItem
    from app.models.workspace import Workspace, WorkspaceStatus

    def _org(prefix: str) -> Organization:
        org = Organization(
            name=prefix, slug=f"{prefix}-{uuid.uuid4().hex[:8]}",
            status=OrganizationStatus.ACTIVE,
        )
        db_session.add(org)
        db_session.flush([org])
        return org

    built: dict[str, dict] = {}
    for label in ("alpha", "beta"):
        org = _org(label)
        workspace = Workspace(
            organization_id=org.id, slug=f"{label}-ws",
            workspace_name=label.title(), status=WorkspaceStatus.ACTIVE,
        )
        db_session.add(workspace)
        db_session.flush([workspace])
        item = WorkItem(
            workspace_id=workspace.id,
            original_filename=f"{label}.pdf",
            stored_filename=f"{org.id}/{uuid.uuid4()}.pdf",
            file_type="application/pdf",
            file_size=1024,
        )
        db_session.add(item)
        db_session.flush([item])
        for index in range(5):
            db_session.add(
                DocumentChunk(
                    workspace_id=workspace.id,
                    organization_id=org.id,
                    work_item_id=item.id,
                    chunk_index=index,
                    page_number=1,
                    content=f"{label} chunk {index} about annual leave accrual",
                    token_count=9,
                    embedding=[0.1 * (index + 1)] * EMBEDDING_DIMENSION,
                    embedding_model="sentence-transformers/all-MiniLM-L6-v2",
                )
            )
        db_session.flush()
        built[label] = {"org": org, "workspace": workspace, "work_item": item}
    return built


def test_scoped_query_returns_only_its_own_workspace(db_session, seeded):
    rows = db_session.execute(
        scoped_chunk_query(db_session, seeded["alpha"]["workspace"].id)
    ).scalars().all()
    assert len(rows) == 5
    assert {row.workspace_id for row in rows} == {seeded["alpha"]["workspace"].id}


def test_small_tenant_gets_the_full_top_k(db_session, seeded):
    """§4's under-return, asserted as a count rather than as tenancy.

    A test that checks 'no foreign chunks came back' passes perfectly when
    zero chunks came back, and zero is exactly what a filtered HNSW scan
    produces for a tenant holding a small share of the index.
    """
    hits = nearest_chunks(
        db_session,
        workspace_id=seeded["beta"]["workspace"].id,
        embedding=[0.2] * EMBEDDING_DIMENSION,
        top_k=5,
    )
    assert len(hits) == 5, "the tenant holds 5 chunks and asked for 5"
    assert all(chunk.workspace_id == seeded["beta"]["workspace"].id
               for chunk, _ in hits)


def test_empty_tenant_returns_nothing_not_a_neighbour(db_session, seeded):
    from app.models.organization import Organization, OrganizationStatus
    from app.models.workspace import Workspace, WorkspaceStatus

    org = Organization(name="gamma", slug=f"gamma-{uuid.uuid4().hex[:8]}",
                       status=OrganizationStatus.ACTIVE)
    db_session.add(org)
    db_session.flush([org])
    empty = Workspace(organization_id=org.id, slug="gamma-ws",
                      workspace_name="Gamma", status=WorkspaceStatus.ACTIVE)
    db_session.add(empty)
    db_session.flush([empty])

    hits = nearest_chunks(
        db_session, workspace_id=empty.id,
        embedding=[0.2] * EMBEDDING_DIMENSION, top_k=5,
    )
    assert hits == []


def test_chunks_cascade_when_the_work_item_goes(db_session, seeded):
    workspace_id = seeded["alpha"]["workspace"].id
    assert count_chunks(db_session, workspace_id=workspace_id) == 5
    db_session.delete(seeded["alpha"]["work_item"])
    db_session.flush()
    assert count_chunks(db_session, workspace_id=workspace_id) == 0


def test_chunks_cascade_when_the_workspace_goes(db_session, seeded):
    """The ARCH-18 erasure path: a DROP, not a sweeper, and not 336 orphans."""
    workspace_id = seeded["beta"]["workspace"].id
    db_session.delete(seeded["beta"]["workspace"])
    db_session.flush()
    assert count_chunks(db_session, workspace_id=workspace_id) == 0


def test_reindex_deletion_is_scoped(db_session, seeded):
    removed = delete_chunks_for_work_item(
        db_session,
        workspace_id=seeded["alpha"]["workspace"].id,
        work_item_id=seeded["alpha"]["work_item"].id,
    )
    assert removed == 5
    assert count_chunks(
        db_session, workspace_id=seeded["beta"]["workspace"].id
    ) == 5


def test_chunk_index_is_unique_per_document(db_session, seeded):
    from sqlalchemy.exc import IntegrityError

    duplicate = DocumentChunk(
        workspace_id=seeded["alpha"]["workspace"].id,
        organization_id=seeded["alpha"]["org"].id,
        work_item_id=seeded["alpha"]["work_item"].id,
        chunk_index=0,
        content="duplicate index",
        token_count=2,
        embedding=[0.5] * EMBEDDING_DIMENSION,
        embedding_model="sentence-transformers/all-MiniLM-L6-v2",
    )
    db_session.add(duplicate)
    with pytest.raises(IntegrityError):
        db_session.flush()
    db_session.rollback()


def test_content_tsv_is_populated_by_the_database(db_session, seeded):
    row = db_session.execute(
        scoped_chunk_query(db_session, seeded["alpha"]["workspace"].id).limit(1)
    ).scalar_one()
    db_session.refresh(row)
    assert row.content_tsv is not None
    assert "accrual" in str(row.content_tsv) or "accru" in str(row.content_tsv)


def test_partitions_exist_and_route_rows(db_session, seeded):
    from sqlalchemy import text as sql_text

    count = db_session.execute(
        sql_text(
            "SELECT count(*) FROM pg_inherits "
            "WHERE inhparent = 'document_chunks'::regclass"
        )
    ).scalar_one()
    assert count == PARTITION_COUNT

    landed = db_session.execute(
        sql_text(
            "SELECT count(DISTINCT tableoid::regclass::text) FROM document_chunks"
        )
    ).scalar_one()
    assert landed >= 1
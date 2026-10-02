"""F-020 — a chunk without a bounding box must be stored, not fail the document.

    pytest tests/services/test_chunk_writer_bbox.py -q

`DocumentChunk.bbox` is a nullable JSONB column guarded by
``CHECK (bbox IS NULL OR jsonb_typeof(bbox) = 'object')``. Assigning Python
``None`` to a plain ``JSONB`` column stores the JSON value ``null``, which is
neither SQL NULL nor an object, so the check rejected the insert and the whole
enrichment job failed. A missing box is a normal outcome (PDF text-layer pages
with no blocks, OCR blocks without a polygon, a chunk that overlaps no block),
so this is the path every such upload takes.
"""

from __future__ import annotations

import uuid

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models.document_chunk import EMBEDDING_DIMENSION
from app.models.work_item import WorkItem
from app.services.chunk_writer import replace_document_chunks
from app.services.document_models import ChunkCandidate
from tests.conftest import Fixture

BOX = {"x0": 10.0, "y0": 20.0, "x1": 110.0, "y1": 45.0, "page": 1}


def _candidate(index: int, bbox: dict | None) -> ChunkCandidate:
    content = f"chunk number {index} of the test document"
    return ChunkCandidate(
        content=content,
        page_number=1,
        chunk_index=index,
        page_start_char=index * 100,
        page_end_char=index * 100 + len(content),
        token_count=len(content.split()),
        bbox=bbox,
    )


def _work_item(db: Session, tenant: Fixture) -> WorkItem:
    item = WorkItem(
        workspace_id=tenant.workspace.id,
        original_filename="no-boxes.pdf",
        stored_filename=f"{tenant.organization.id}/{uuid.uuid4()}.pdf",
        file_type="application/pdf",
        file_size=1024,
    )
    db.add(item)
    db.flush([item])
    return item


def test_chunks_without_a_bbox_are_written(db_session: Session, tenant: Fixture) -> None:
    item = _work_item(db_session, tenant)
    candidates = [_candidate(0, None), _candidate(1, BOX), _candidate(2, None)]

    summary = replace_document_chunks(
        db_session,
        workspace_id=tenant.workspace.id,
        organization_id=tenant.organization.id,
        work_item_id=item.id,
        uploaded_file_id=None,
        candidates=candidates,
        embeddings=[[0.01] * EMBEDDING_DIMENSION for _ in candidates],
    )

    assert summary["chunks_written"] == 3
    assert summary["boxed_chunks"] == 1


def test_a_missing_bbox_is_sql_null_not_json_null(
    db_session: Session, tenant: Fixture
) -> None:
    """`bbox IS NULL` is how the rest of the code, and the constraint, ask."""
    item = _work_item(db_session, tenant)
    candidates = [_candidate(0, None), _candidate(1, BOX)]
    replace_document_chunks(
        db_session,
        workspace_id=tenant.workspace.id,
        organization_id=tenant.organization.id,
        work_item_id=item.id,
        uploaded_file_id=None,
        candidates=candidates,
        embeddings=[[0.01] * EMBEDDING_DIMENSION for _ in candidates],
    )

    rows = db_session.execute(
        text(
            "SELECT chunk_index, bbox IS NULL AS is_sql_null, "
            "jsonb_typeof(bbox) AS json_type "
            "FROM document_chunks WHERE work_item_id = :wid ORDER BY chunk_index"
        ),
        {"wid": item.id},
    ).all()

    assert [(r.chunk_index, r.is_sql_null, r.json_type) for r in rows] == [
        (0, True, None),
        (1, False, "object"),
    ]

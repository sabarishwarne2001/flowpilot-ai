"""F-020 regression: a chunk with no bounding box must be stored, not rejected.

A missing box is a normal outcome (text-layer PDFs, OCR blocks without a
polygon, a chunk that overlaps no block). It used to be written as the JSON
value ``null`` instead of SQL ``NULL``. The table's check constraint
``ck_document_chunks_bbox_is_object`` allows only SQL NULL or a JSON object,
so the insert raised ``CheckViolation`` and the whole document failed.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.models.document_chunk import EMBEDDING_DIMENSION, DocumentChunk
from app.services.chunk_writer import replace_document_chunks
from app.services.document_models import ChunkCandidate


@pytest.fixture()
def scope(db_session: Session):
    from app.models.organization import Organization, OrganizationStatus
    from app.models.work_item import WorkItem
    from app.models.workspace import Workspace, WorkspaceStatus

    org = Organization(
        name="bbox-null",
        slug=f"bbox-{uuid.uuid4().hex[:8]}",
        status=OrganizationStatus.ACTIVE,
    )
    db_session.add(org)
    db_session.flush([org])
    workspace = Workspace(
        organization_id=org.id,
        slug="bbox-ws",
        workspace_name="Bbox",
        status=WorkspaceStatus.ACTIVE,
    )
    db_session.add(workspace)
    db_session.flush([workspace])
    item = WorkItem(
        workspace_id=workspace.id,
        original_filename="no-boxes.pdf",
        stored_filename=f"{org.id}/{uuid.uuid4()}.pdf",
        file_type="application/pdf",
        file_size=1024,
        extracted_text="alpha beta",
    )
    db_session.add(item)
    db_session.flush([item])
    return org, workspace, item


def _candidate(index: int, bbox=None) -> ChunkCandidate:
    content = f"chunk number {index}"
    return ChunkCandidate(
        content=content,
        page_number=1,
        chunk_index=index,
        page_start_char=0,
        page_end_char=len(content),
        token_count=3,
        bbox=bbox,
    )


def test_chunk_without_bbox_is_stored_as_sql_null(db_session: Session, scope):
    org, workspace, item = scope

    summary = replace_document_chunks(
        db_session,
        workspace_id=workspace.id,
        organization_id=org.id,
        work_item_id=item.id,
        uploaded_file_id=None,
        candidates=[_candidate(0), _candidate(1, bbox={"page": 1, "x0": 0, "y0": 0, "x1": 1, "y1": 1})],
        embeddings=[[0.1] * EMBEDDING_DIMENSION, [0.2] * EMBEDDING_DIMENSION],
        embedding_model="test-model",
    )

    assert summary["chunks_written"] == 2
    assert summary["boxed_chunks"] == 1

    # The stored value must be SQL NULL, not the JSON value `null`.
    kinds = dict(
        db_session.execute(
            text(
                "SELECT chunk_index, "
                "       CASE WHEN bbox IS NULL THEN 'sql_null' "
                "            ELSE jsonb_typeof(bbox) END "
                "FROM document_chunks WHERE work_item_id = :wid"
            ),
            {"wid": item.id},
        ).all()
    )
    assert kinds == {0: "sql_null", 1: "object"}


def test_direct_orm_insert_with_none_bbox_does_not_violate_constraint(
    db_session: Session, scope
):
    org, workspace, item = scope

    db_session.add(
        DocumentChunk(
            workspace_id=workspace.id,
            organization_id=org.id,
            work_item_id=item.id,
            chunk_index=0,
            page_number=1,
            content="plain",
            token_count=1,
            bbox=None,
            embedding=[0.3] * EMBEDDING_DIMENSION,
            embedding_model="test-model",
        )
    )
    db_session.flush()

    row = db_session.execute(
        select(DocumentChunk).where(DocumentChunk.work_item_id == item.id)
    ).scalar_one()
    assert row.bbox is None


# ---------------------------------------------------------------------------
# Same mistake, other columns: each is a nullable JSONB column guarded by a
# "IS NULL OR jsonb_typeof(...) = 'object'" check constraint.
# ---------------------------------------------------------------------------


def test_usage_event_without_details_is_stored_as_sql_null(db_session: Session, scope):
    from decimal import Decimal

    from app.models.usage_event import UsageEvent

    org, _workspace, _item = scope
    event = UsageEvent(
        organization_id=org.id,
        event_type="document.processed",
        unit="page",
        quantity=Decimal("1"),
        details=None,
    )
    db_session.add(event)
    db_session.flush()

    stored = db_session.execute(
        text("SELECT details IS NULL FROM usage_events WHERE id = :i"),
        {"i": event.id},
    ).scalar_one()
    assert stored is True


def test_automation_rule_without_flow_spec_is_stored_as_sql_null(
    db_session: Session, rule_factory
):
    rule = rule_factory()
    rule.flow_spec = None
    db_session.flush()

    stored = db_session.execute(
        text("SELECT flow_spec IS NULL FROM automation_rules WHERE id = :i"),
        {"i": rule.id},
    ).scalar_one()
    assert stored is True

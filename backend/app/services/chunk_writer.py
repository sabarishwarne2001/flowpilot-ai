"""ARCH-11 Steps 3-4 — transactional write path into `document_chunks`."""

from __future__ import annotations

import logging
import uuid
from typing import Any, Optional, Sequence

from sqlalchemy.orm import Session

from app.core.embeddings import active_model_name
from app.db.chunk_scope import delete_chunks_for_work_item
from app.models.document_chunk import DocumentChunk
from app.services.document_models import ChunkCandidate

logger = logging.getLogger("app.services.chunk_writer")


class ChunkWriteError(RuntimeError):
    """Refusal to persist misaligned chunk data."""


def replace_document_chunks(
    db: Session,
    *,
    workspace_id: uuid.UUID,
    organization_id: uuid.UUID,
    work_item_id: uuid.UUID,
    uploaded_file_id: Optional[uuid.UUID],
    candidates: Sequence[ChunkCandidate],
    embeddings: Sequence[Sequence[float]],
    embedding_model: Optional[str] = None,
) -> dict[str, Any]:
    """Delete previous chunks for this document and insert new candidates."""
    if len(candidates) != len(embeddings):
        raise ChunkWriteError(
            f"{len(candidates)} chunks against {len(embeddings)} embeddings. "
            "Refusing to store misaligned corpus."
        )

    model = embedding_model or active_model_name()
    removed = delete_chunks_for_work_item(
        db, workspace_id=workspace_id, work_item_id=work_item_id
    )
    db.flush()

    rows = [
        DocumentChunk(
            workspace_id=workspace_id,
            organization_id=organization_id,
            work_item_id=work_item_id,
            uploaded_file_id=uploaded_file_id,
            chunk_index=candidate.chunk_index,
            page_number=candidate.page_number,
            page_start_char=candidate.page_start_char,
            page_end_char=candidate.page_end_char,
            content=candidate.content,
            token_count=candidate.token_count,
            bbox=candidate.bbox,
            embedding=list(vector),
            embedding_model=model,
        )
        for candidate, vector in zip(candidates, embeddings)
    ]
    db.add_all(rows)
    db.flush()

    summary = {
        "chunks_written": len(rows),
        "chunks_removed": removed,
        "boxed_chunks": sum(1 for candidate in candidates if candidate.bbox),
        "embedding_model": model,
    }
    logger.info(
        "chunks.replaced",
        extra={
            "work_item_id": str(work_item_id),
            "workspace_id": str(workspace_id),
            **summary,
        },
    )
    return summary


def indexed_work_item_ids(
    db: Session, *, workspace_id: uuid.UUID
) -> set[uuid.UUID]:
    """Find work items in the workspace that already have rows in document_chunks."""
    from sqlalchemy import distinct
    from app.db.chunk_scope import scoped_chunk_query

    statement = scoped_chunk_query(
        db, workspace_id, entity=distinct(DocumentChunk.work_item_id)
    )
    return {row for row in db.execute(statement).scalars().all()}


__all__ = [
    "ChunkWriteError",
    "indexed_work_item_ids",
    "replace_document_chunks",
]

"""ARCH-11 Step 2, ARCH-21 §3.3 — scoped chunk query building and iterative scan."""

from __future__ import annotations

import logging
import uuid
from typing import Any, Optional, Sequence

from sqlalchemy import Select, select, text
from sqlalchemy.orm import Session

from app.core.api_tiers import clamp_ef_search
from app.core.config import settings
from app.models.document_chunk import DocumentChunk

logger = logging.getLogger("app.db.chunk_scope")

COSINE_DISTANCE = "<=>"


class VectorScopeError(ValueError):
    pass


def _require_uuid(value: Any, field: str) -> uuid.UUID:
    if isinstance(value, uuid.UUID):
        return value
    if value is None:
        raise VectorScopeError(f"{field} is required.")
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError) as exc:
        raise VectorScopeError(f"{field}={value!r} is not a UUID") from exc


def ensure_iterative_scan(
    db: Session, *, ef_search: Optional[int] = None
) -> int:
    applied = clamp_ef_search(
        settings.HNSW_EF_SEARCH if ef_search is None else ef_search
    )

    if not settings.APPLY_HNSW_SESSION_DEFAULTS:
        return applied
    try:
        db.execute(
            text("SET LOCAL hnsw.iterative_scan = :mode").bindparams(
                mode=settings.HNSW_ITERATIVE_SCAN
            )
        )
        db.execute(
            text("SET LOCAL hnsw.ef_search = :ef").bindparams(ef=applied)
        )
    except Exception:
        logger.warning(
            "hnsw.iterative_scan could not be applied; filtered vector "
            "queries may under-return.",
            exc_info=True,
        )
    return applied


def scoped_chunk_query(
    db: Session,
    workspace_id: uuid.UUID | str,
    *,
    organization_id: Optional[uuid.UUID | str] = None,
    work_item_ids: Optional[Sequence[uuid.UUID | str]] = None,
    entity: Any = DocumentChunk,
) -> Select:
    workspace = _require_uuid(workspace_id, "workspace_id")
    columns = entity if isinstance(entity, (tuple, list)) else (entity,)
    statement = select(*columns).where(DocumentChunk.workspace_id == workspace)

    if organization_id is not None:
        statement = statement.where(
            DocumentChunk.organization_id == _require_uuid(organization_id, "organization_id")
        )

    if work_item_ids is not None:
        wanted = [_require_uuid(value, "work_item_id") for value in work_item_ids]
        if not wanted:
            return statement.where(text("false"))
        statement = statement.where(DocumentChunk.work_item_id.in_(wanted))

    return statement


def nearest_chunks(
    db: Session,
    *,
    workspace_id: uuid.UUID | str,
    embedding: Sequence[float],
    top_k: int,
    organization_id: Optional[uuid.UUID | str] = None,
    work_item_ids: Optional[Sequence[uuid.UUID | str]] = None,
    max_distance: Optional[float] = None,
    ef_search: Optional[int] = None,
) -> list[tuple[DocumentChunk, float]]:
    if top_k <= 0:
        raise VectorScopeError("top_k must be > 0")

    ensure_iterative_scan(db, ef_search=ef_search)

    distance = DocumentChunk.embedding.cosine_distance(list(embedding))
    statement = scoped_chunk_query(
        db,
        workspace_id,
        organization_id=organization_id,
        work_item_ids=work_item_ids,
        entity=(DocumentChunk, distance.label("distance")),
    )
    if max_distance is not None:
        statement = statement.where(distance <= max_distance)

    rows = db.execute(statement.order_by(distance).limit(top_k)).all()
    return [(row[0], float(row[1])) for row in rows]


def count_chunks(
    db: Session,
    *,
    workspace_id: uuid.UUID | str,
    work_item_ids: Optional[Sequence[uuid.UUID | str]] = None,
) -> int:
    from sqlalchemy import func

    statement = scoped_chunk_query(
        db, workspace_id, work_item_ids=work_item_ids, entity=func.count()
    )
    return int(db.execute(statement).scalar_one())


def delete_chunks_for_work_item(
    db: Session, *, workspace_id: uuid.UUID | str, work_item_id: uuid.UUID | str
) -> int:
    from sqlalchemy import delete

    workspace = _require_uuid(workspace_id, "workspace_id")
    item = _require_uuid(work_item_id, "work_item_id")
    result = db.execute(
        delete(DocumentChunk).where(
            DocumentChunk.workspace_id == workspace,
            DocumentChunk.work_item_id == item,
        )
    )
    return int(result.rowcount or 0)


__all__ = [
    "COSINE_DISTANCE",
    "VectorScopeError",
    "count_chunks",
    "delete_chunks_for_work_item",
    "ensure_iterative_scan",
    "nearest_chunks",
    "scoped_chunk_query",
]

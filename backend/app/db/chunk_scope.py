"""ARCH-11 Step 2 — the one place a `document_chunks` query is built.

This module exists because of a failure mode that does not announce itself. A
`pgvector` similarity search that omits the workspace predicate returns the
nearest neighbours **across all tenants**. It does not error. It does not look
wrong. It quietly returns another company's document as a citation, ranked
first, with a plausible page number attached.

ARCH-02 solved this class of problem for relational reads by deleting the CRUD
signatures that took `user_id`, so every call site failed to compile. The
equivalent here is narrower: there is no compiler to lean on, so the discipline
is a single helper plus a static scan (`tests/services/test_vector_scoping.py`)
that fails the build when `document_chunks` or `DocumentChunk` is referenced
outside the allow-list.

There is a **second** failure, and it is the one nobody warns you about: *with*
the predicate present, a filtered HNSW query silently returns too few rows.
HNSW searches the index and applies `WHERE` afterwards, so a tenant holding a
small share of the index gets a candidate set that is almost entirely other
tenants' rows, which are filtered away. Measured on 100,020 vectors across 60
workspaces: a 1.7% tenant asked for 10 and got 5; a 0.02% tenant got **0**, and
was still at 0 with `ef_search` raised to 400.

Both halves of the mitigation live here rather than at the call sites:
partition pruning (from the `workspace_id` equality predicate, which is also
the partition key) and `hnsw.iterative_scan`, asserted per transaction by
`ensure_iterative_scan()`.

`Step 8's gate must assert result count, not just result tenancy.` A test that
checks "no foreign chunks came back" passes perfectly when zero chunks came
back, and zero is exactly what the under-return produces.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, Iterable, Optional, Sequence

from sqlalchemy import Select, select, text
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.document_chunk import DocumentChunk

logger = logging.getLogger("app.db.chunk_scope")

#: Distance operators. `<=>` is cosine, matching `vector_cosine_ops` on the
#: HNSW index. Using a different operator here silently disables the index —
#: the query still returns correct rows, by sequential scan, at 100x the cost.
COSINE_DISTANCE = "<=>"


class VectorScopeError(ValueError):
    """A chunk query was requested without a usable tenancy predicate."""


def _require_uuid(value: Any, field: str) -> uuid.UUID:
    if isinstance(value, uuid.UUID):
        return value
    if value is None:
        raise VectorScopeError(
            f"{field} is required. There is no unscoped chunk query; a "
            "similarity search without the workspace predicate returns the "
            "nearest neighbours across every tenant on the platform."
        )
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError) as exc:
        raise VectorScopeError(f"{field}={value!r} is not a UUID") from exc


def ensure_iterative_scan(db: Session) -> None:
    """Apply `hnsw.iterative_scan` for this transaction.

    The connect hook in `app/db/session.py` sets it per connection, which
    covers the application. This exists for the two cases the hook does not:
    a session built on a connection created before the hook was added, and the
    test suite's `db_session`, which binds to a connection opened by the test
    fixture. Cheap enough to call on every retrieval; a `SET LOCAL` is not a
    round trip worth optimising away when the alternative is under-returning.
    """
    if not settings.APPLY_HNSW_SESSION_DEFAULTS:
        return
    try:
        db.execute(
            text("SET LOCAL hnsw.iterative_scan = :mode").bindparams(
                mode=settings.HNSW_ITERATIVE_SCAN
            )
        )
        db.execute(
            text("SET LOCAL hnsw.ef_search = :ef").bindparams(
                ef=int(settings.HNSW_EF_SEARCH)
            )
        )
    except Exception:  # noqa: BLE001
        logger.warning(
            "hnsw.iterative_scan could not be applied; a small tenant may "
            "receive fewer chunks than requested (ARCH-11 §4).",
            exc_info=True,
        )


# ===========================================================================
# The helper
# ===========================================================================


def scoped_chunk_query(
    db: Session,
    workspace_id: uuid.UUID | str,
    *,
    organization_id: Optional[uuid.UUID | str] = None,
    work_item_ids: Optional[Sequence[uuid.UUID | str]] = None,
    entity: Any = DocumentChunk,
) -> Select:
    """Return a `Select` over `document_chunks` that is already tenant-scoped.

    `db` is taken but not used to execute anything. It is in the signature on
    purpose: it makes the helper the natural thing to reach for at a call site
    that already has a session, and it gives `ensure_iterative_scan` somewhere
    to live when a caller wants the statement and the GUC together.

    `organization_id`, when supplied, is a second predicate over the
    denormalised column. It is redundant with `workspace_id` — a workspace
    belongs to exactly one organization — and it is worth adding on any path
    reached from an organization-scoped route, because a redundant predicate
    that can only ever be true is free, and it turns a hypothetical
    workspace-to-org mismatch from a leak into an empty result.
    """
    workspace = _require_uuid(workspace_id, "workspace_id")

    columns = entity if isinstance(entity, (tuple, list)) else (entity,)
    statement = select(*columns).where(DocumentChunk.workspace_id == workspace)

    if organization_id is not None:
        statement = statement.where(
            DocumentChunk.organization_id
            == _require_uuid(organization_id, "organization_id")
        )

    if work_item_ids is not None:
        wanted = [_require_uuid(value, "work_item_id") for value in work_item_ids]
        if not wanted:
            # An empty filter list means "no documents are searchable", which
            # is a real state (a workspace whose documents are all still
            # extracting). Returning an always-false predicate is correct;
            # dropping the filter would widen the query to the whole workspace.
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
) -> list[tuple[DocumentChunk, float]]:
    """Dense retrieval, scoped, ordered by cosine distance ascending.

    Returns `(chunk, distance)` rather than a bare list so the caller can turn
    distance into whatever similarity convention it already uses without
    guessing which operator produced it.
    """
    if top_k <= 0:
        raise VectorScopeError("top_k must be > 0")

    ensure_iterative_scan(db)

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
    """Explicit deletion for re-indexing. Ordinary deletion is the cascade.

    This is not the path that removes a deleted document's chunks — that is the
    foreign key, and it is the point of the migration. This exists for Step 4's
    reindex, where a document is re-chunked in place and its previous chunks
    must go without the document going with them.
    """
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
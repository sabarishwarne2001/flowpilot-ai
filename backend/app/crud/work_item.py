"""
Repository layer for work items, scoped to a workspace.

Every read here takes workspace_id and applies it as a WHERE clause. That is
the entire isolation guarantee at this layer — there is no ambient tenant, no
session variable, and no row-level security policy underneath. A function that
omits the filter returns another tenant's documents and nothing downstream
will notice, because the rows are structurally valid.

created_by_user_id is attribution, not scope. It is written on create and may
NARROW a query already scoped to a workspace, but it never scopes one on its
own. A document belongs to the workspace and remains the workspace's after its
uploader leaves.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from app.models.work_item import WorkItem
from app.schemas.work_item import WorkItemCreate, WorkItemStatus, WorkItemUpdate


#: Sortable columns, allow-listed. The previous implementation resolved
#: sort_by through getattr() against the model, which let a query parameter
#: name any attribute on the class. Harmless in practice today, but it is an
#: unbounded surface reachable from an unauthenticated-shaped input, and this
#: rewrite is the cheapest moment to close it.
SORTABLE_COLUMNS: frozenset[str] = frozenset({
    "created_at", "updated_at", "original_filename", "file_size", "status",
})


def _scoped(workspace_id: uuid.UUID) -> Select[tuple[WorkItem]]:
    """
    Base statement for every read in this module.

    Exists so that the workspace predicate is written once. A reviewer
    checking this file for isolation reads one function rather than eleven.
    """
    return select(WorkItem).where(WorkItem.workspace_id == workspace_id)


# ---------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------

def get_work_item(
    db: Session,
    *,
    workspace_id: uuid.UUID,
    work_item_id: uuid.UUID,
) -> WorkItem | None:
    """
    Fetches one work item from within a workspace.

    Returns None rather than raising when the item belongs to another
    workspace, so the caller cannot distinguish "does not exist" from "exists
    elsewhere" — the same reasoning that makes get_workspace_context return
    404 instead of 403.
    """
    statement = _scoped(workspace_id).where(WorkItem.id == work_item_id)
    return db.execute(statement).scalar_one_or_none()


def list_work_items(
    db: Session,
    *,
    workspace_id: uuid.UUID,
    skip: int = 0,
    limit: int = 100,
    search: str | None = None,
    status: WorkItemStatus | None = None,
    created_by_user_id: uuid.UUID | None = None,
    sort_by: str = "created_at",
    sort_order: str = "desc",
) -> list[WorkItem]:
    """
    Lists work items in a workspace.

    created_by_user_id narrows an already-scoped query — it drives a "my
    uploads" filter, and it is the caller's choice, never a security boundary.
    Whether a given role is *permitted* to see the whole workspace is a policy
    question that belongs in the service layer, which has the TenantContext
    and therefore the effective role. This layer does not import WorkspaceRole.
    """
    limit = min(limit, 100)
    statement = _scoped(workspace_id)

    if search:
        statement = statement.where(
            WorkItem.original_filename.ilike(f"%{search}%")
        )
    if status:
        statement = statement.where(WorkItem.status == status)
    if created_by_user_id:
        statement = statement.where(
            WorkItem.created_by_user_id == created_by_user_id
        )

    column = sort_by if sort_by in SORTABLE_COLUMNS else "created_at"
    sort_column = getattr(WorkItem, column)
    statement = statement.order_by(
        sort_column.asc() if sort_order.lower() == "asc" else sort_column.desc()
    )

    statement = statement.offset(skip).limit(limit)
    return list(db.execute(statement).scalars().all())


def count_work_items(
    db: Session,
    *,
    workspace_id: uuid.UUID,
    created_by_user_id: uuid.UUID | None = None,
) -> int:
    statement = (
        select(func.count())
        .select_from(WorkItem)
        .where(WorkItem.workspace_id == workspace_id)
    )
    if created_by_user_id:
        statement = statement.where(
            WorkItem.created_by_user_id == created_by_user_id
        )
    return db.execute(statement).scalar_one()


def get_recent_work_items(
    db: Session,
    *,
    workspace_id: uuid.UUID,
    limit: int = 10,
) -> list[WorkItem]:
    statement = (
        _scoped(workspace_id)
        .order_by(WorkItem.updated_at.desc())
        .limit(limit)
    )
    return list(db.execute(statement).scalars().all())


# ---------------------------------------------------------------------
# Aggregates
# ---------------------------------------------------------------------

def _count_by_status(
    db: Session, *, workspace_id: uuid.UUID, status: WorkItemStatus
) -> int:
    statement = (
        select(func.count())
        .select_from(WorkItem)
        .where(
            WorkItem.workspace_id == workspace_id,
            WorkItem.status == status,
        )
    )
    return db.scalar(statement) or 0


def get_processing_status(
    db: Session, *, workspace_id: uuid.UUID
) -> tuple[int, int]:
    """Returns (queued, processing) for the workspace."""
    return (
        _count_by_status(db, workspace_id=workspace_id,
                         status=WorkItemStatus.QUEUED),
        _count_by_status(db, workspace_id=workspace_id,
                         status=WorkItemStatus.PROCESSING),
    )


def get_completion_statistics(
    db: Session, *, workspace_id: uuid.UUID
) -> tuple[int, int]:
    """Returns (completed, failed) for the workspace."""
    return (
        _count_by_status(db, workspace_id=workspace_id,
                         status=WorkItemStatus.COMPLETED),
        _count_by_status(db, workspace_id=workspace_id,
                         status=WorkItemStatus.FAILED),
    )


def count_completed_today(db: Session, *, workspace_id: uuid.UUID) -> int:
    """
    Documents completed today.

    NOTE: "today" is evaluated in the database session's timezone, not the
    workspace's. Workspace.timezone exists and is not consulted here, so a
    workspace in Asia/Kolkata sees a UTC day boundary. Pre-existing, unchanged
    by this step, and worth a ticket — see the follow-ups.
    """
    today = datetime.now(timezone.utc).date()
    statement = (
        select(func.count())
        .select_from(WorkItem)
        .where(
            WorkItem.workspace_id == workspace_id,
            WorkItem.status == WorkItemStatus.COMPLETED,
            func.date(WorkItem.updated_at) == today,
        )
    )
    return db.scalar(statement) or 0


def get_document_type_distribution(
    db: Session, *, workspace_id: uuid.UUID
) -> list[tuple[str, int]]:
    statement = (
        select(WorkItem.file_type, func.count())
        .where(WorkItem.workspace_id == workspace_id)
        .group_by(WorkItem.file_type)
    )
    return list(db.execute(statement).all())


# ---------------------------------------------------------------------
# Writes
# ---------------------------------------------------------------------

def create_work_item(
    db: Session,
    *,
    workspace_id: uuid.UUID,
    created_by_user_id: uuid.UUID,
    obj_in: WorkItemCreate,
) -> WorkItem:
    """
    Both identifiers are required and they are not interchangeable.
    workspace_id determines who can see the document; created_by_user_id
    records who uploaded it and may become NULL later without affecting
    visibility.
    """
    db_obj = WorkItem(
        original_filename=obj_in.original_filename,
        stored_filename=obj_in.stored_filename,
        file_type=obj_in.file_type,
        file_size=obj_in.file_size,
        workspace_id=workspace_id,
        created_by_user_id=created_by_user_id,
    )
    db.add(db_obj)
    db.commit()
    db.refresh(db_obj)
    return db_obj


def update_work_item_state(
    db: Session, *, db_obj: WorkItem, obj_in: WorkItemUpdate
) -> WorkItem:
    """
    Takes an already-fetched instance, so no workspace_id parameter: the
    object can only have come from get_work_item, which applied the filter.
    Re-checking here would suggest the caller might have obtained it some
    other way.
    """
    for field, value in obj_in.model_dump(exclude_unset=True).items():
        setattr(db_obj, field, value)
    db.add(db_obj)
    db.commit()
    db.refresh(db_obj)
    return db_obj


def delete_work_item(db: Session, *, db_obj: WorkItem) -> None:
    db.delete(db_obj)
    db.commit()

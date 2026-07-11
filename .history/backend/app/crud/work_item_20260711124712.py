"""
Database CRUD (Create, Read, Update, Delete) repository layer for Work Items.

Handles direct transactional statements and queries targeting the work_items table, 
maintaining strict separation from business pipelines or routers.
"""

import uuid
from typing import Any, Union
from datetime import datetime, timezone
from sqlalchemy import select, func
from sqlalchemy.orm import Session
from app.models.work_item import WorkItem
from app.schemas.work_item import WorkItemCreate, WorkItemUpdate, WorkItemStatus



def get_work_item_by_id(
    db: Session, 
    *, 
    work_item_id: uuid.UUID, 
    user_id: uuid.UUID
) -> Union[WorkItem, None]:
    """
    Retrieves a single WorkItem record belonging to a specific user.
    
    Enforces operational boundaries, preventing unauthorized data access.
    """
    statement = select(WorkItem).where(
        WorkItem.id == work_item_id,
        WorkItem.user_id == user_id
    )
    return db.execute(statement).scalar_one_or_none()


def get_work_items_for_user(
    db: Session,
    *,
    user_id: uuid.UUID,
    skip: int = 0,
    limit: int = 100,
    search: str | None = None,
    status: WorkItemStatus | None = None,
    sort_by: str = "created_at",
    sort_order: str = "desc",
) -> list[WorkItem]:
    """
    Retrieves a paginated list of WorkItems belonging to a user,
    with optional search, status filtering, sorting and pagination.
    """

    limit = min(limit, 100)

    statement = select(WorkItem).where(
        WorkItem.user_id == user_id
    )

    #
    # Search
    #
    if search:
        statement = statement.where(
            WorkItem.original_filename.ilike(f"%{search}%")
        )

    #
    # Status filter
    #
    if status:
        statement = statement.where(
            WorkItem.status == status
        )

    #
    # Sorting
    #
    sort_column = getattr(
        WorkItem,
        sort_by,
        WorkItem.created_at,
    )

    if sort_order.lower() == "asc":
        statement = statement.order_by(sort_column.asc())
    else:
        statement = statement.order_by(sort_column.desc())

    #
    # Pagination
    #
    statement = (
        statement
        .offset(skip)
        .limit(limit)
    )

    return list(
        db.execute(statement)
        .scalars()
        .all()
    )

def count_work_items_for_user(
    db: Session,
    *,
    user_id: uuid.UUID,
) -> int:
    """
    Returns the total number of WorkItems owned by a user.
    """

    statement = (
        select(func.count())
        .select_from(WorkItem)
        .where(WorkItem.user_id == user_id)
    )

    return db.execute(statement).scalar_one()

def count_processed_today_for_user(
    db: Session,
    *,
    user_id: uuid.UUID,
) -> int:
    """
    Returns the number of documents completed today.
    """

    today = datetime.now(timezone.utc).date()

    statement = (
        select(func.count())
        .select_from(WorkItem)
        .where(
            WorkItem.user_id == user_id,
            WorkItem.status == "COMPLETED",
            func.date(WorkItem.updated_at) == today,
        )
    )

    return db.scalar(statement) or 0

def create_work_item(
    db: Session, 
    *, 
    obj_in: WorkItemCreate, 
    user_id: uuid.UUID
) -> WorkItem:
    """
    Instantiates and persists a new child WorkItem record linked to a parent User.
    """
    db_obj = WorkItem(
        original_filename=obj_in.original_filename,
        stored_filename=obj_in.stored_filename,
        file_type=obj_in.file_type,
        file_size=obj_in.file_size,
        user_id=user_id
    )
    db.add(db_obj)
    db.commit()
    db.refresh(db_obj)
    return db_obj


def update_work_item_state(
    db: Session, 
    *, 
    db_obj: WorkItem, 
    obj_in: WorkItemUpdate
) -> WorkItem:
    """
    Performs partial updates on an active WorkItem (such as setting state or summaries).
    """
    # Exclude unset fields to support partial patch/updates cleanly
    update_data = obj_in.model_dump(exclude_unset=True)
    
    for field, value in update_data.items():
        setattr(db_obj, field, value)
    db.add(db_obj)
    db.commit()
    db.refresh(db_obj)
    return db_obj

def delete_work_item(
    db: Session,
    *,
    db_obj: WorkItem,
) -> None:
    """
    Permanently deletes a work item.
    """

    db.delete(db_obj)
    db.commit()


def get_document_type_distribution(
    db: Session,
    *,
    user_id: uuid.UUID,
) -> list[tuple[str, int]]:
    """
    Returns the number of documents grouped by MIME type.
    """

    statement = (
        select(
            WorkItem.file_type,
            func.count()
        )
        .where(WorkItem.user_id == user_id)
        .group_by(WorkItem.file_type)
    )

    return list(db.execute(statement).all())

def get_recent_work_items(
    db: Session,
    *,
    user_id: uuid.UUID,
    limit: int = 10,
) -> list[WorkItem]:
    """
    Returns the most recent work items for a user.
    """

    statement = (
        select(WorkItem)
        .where(WorkItem.user_id == user_id)
        .order_by(WorkItem.updated_at.desc())
        .limit(limit)
    )

    return list(db.execute(statement).scalars().all())

def get_processing_status(
    db: Session,
    *,
    user_id: uuid.UUID,
) -> tuple[int, int]:
    """
    Returns the number of queued and processing work items
    for the authenticated user.
    """

    queued_statement = (
        select(func.count())
        .select_from(WorkItem)
        .where(
            WorkItem.user_id == user_id,
            WorkItem.status == "QUEUED",
        )
    )

    processing_statement = (
        select(func.count())
        .select_from(WorkItem)
        .where(
            WorkItem.user_id == user_id,
            WorkItem.status == "PROCESSING",
        )
    )

    queued = db.scalar(queued_statement) or 0
    processing = db.scalar(processing_statement) or 0

    return queued, processing

def get_completion_statistics(
    db: Session,
    *,
    user_id: uuid.UUID,
) -> tuple[int, int]:
    """
    Returns the number of completed and failed work items.
    """

    completed_statement = (
        select(func.count())
        .select_from(WorkItem)
        .where(
            WorkItem.user_id == user_id,
            WorkItem.status == "COMPLETED",
        )
    )

    failed_statement = (
        select(func.count())
        .select_from(WorkItem)
        .where(
            WorkItem.user_id == user_id,
            WorkItem.status == "FAILED",
        )
    )

    completed = db.scalar(completed_statement) or 0
    failed = db.scalar(failed_statement) or 0

    return completed, failed
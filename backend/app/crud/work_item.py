"""
Database CRUD (Create, Read, Update, Delete) repository layer for Work Items.

Handles direct transactional statements and queries targeting the work_items table, 
maintaining strict separation from business pipelines or routers.
"""

import uuid
from typing import Any, Union
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.models.work_item import WorkItem
from app.schemas.work_item import WorkItemCreate, WorkItemUpdate


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
    limit: int = 100
) -> list[WorkItem]:
    
    limit = min(limit, 100)
    """
    Retrieves a paginated, chronologically descending list of WorkItems belonging to a user.
    """
    statement = (
        select(WorkItem)
        .where(WorkItem.user_id == user_id)
        .offset(skip)
        .limit(limit)
        .order_by(WorkItem.created_at.desc())
    )
    return list(db.execute(statement).scalars().all())


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
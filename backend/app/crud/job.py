"""
Database CRUD (Create, Read, Update, Delete) repository layer for Processing Jobs.

Handles database transactional statements and queries targeting the processing_jobs table, 
enforcing strict user ownership boundaries during execution checks.
"""

import uuid
from typing import Union
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.models.job import ProcessingJob
from app.models.work_item import WorkItem
from app.schemas.job import JobCreate, JobUpdate


def get_job_by_id(
    db: Session, 
    *, 
    job_id: uuid.UUID, 
    user_id: uuid.UUID
) -> Union[ProcessingJob, None]:
    """
    Retrieves a single ProcessingJob record by UUID.
    
    Enforces privacy by joining with the parent WorkItem and validating user_id ownership.
    """
    statement = (
        select(ProcessingJob)
        .join(WorkItem, ProcessingJob.work_item_id == WorkItem.id)
        .where(
            ProcessingJob.id == job_id,
            WorkItem.user_id == user_id
        )
    )
    return db.execute(statement).scalar_one_or_none()


def get_jobs_for_work_item(
    db: Session, 
    *, 
    work_item_id: uuid.UUID, 
    user_id: uuid.UUID
) -> list[ProcessingJob]:
    """
    Retrieves all ProcessingJob runs registered for a specific parent WorkItem.
    
    Validates that the parent WorkItem belongs to the requesting user_id.
    """
    statement = (
        select(ProcessingJob)
        .join(WorkItem, ProcessingJob.work_item_id == WorkItem.id)
        .where(
            ProcessingJob.work_item_id == work_item_id,
            WorkItem.user_id == user_id
        )
        .order_by(ProcessingJob.created_at.desc())
    )
    return list(db.execute(statement).scalars().all())


def create_job(db: Session, *, obj_in: JobCreate) -> ProcessingJob:
    """
    Instantiates and persists a new ProcessingJob execution run linked to a parent WorkItem.
    """
    db_obj = ProcessingJob(
        work_item_id=obj_in.work_item_id
    )
    db.add(db_obj)
    db.commit()
    db.refresh(db_obj)
    return db_obj


def update_job(
    db: Session, 
    *, 
    db_obj: ProcessingJob, 
    obj_in: JobUpdate
) -> ProcessingJob:
    """
    Performs incremental partial updates on a running background ProcessingJob.
    """
    update_data = obj_in.model_dump(exclude_unset=True)
    
    # Translate Pydantic schema 'meta_data' to model 'execution_metadata'
    if "metadata" in update_data:
        db_obj.execution_metadata = update_data.pop("metadata")
        
    for field, value in update_data.items():
        if hasattr(db_obj, field):
            setattr(db_obj, field, value)
            
    db.add(db_obj)
    db.commit()
    db.refresh(db_obj)
    return db_obj
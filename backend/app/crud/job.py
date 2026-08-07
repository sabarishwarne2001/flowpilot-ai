import uuid
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.models.job import ProcessingJob
from app.models.work_item import WorkItem
from app.schemas.job import JobCreate, JobUpdate

def get_job_by_id(
    db: Session,
    *,
    workspace_id: uuid.UUID,
    job_id: uuid.UUID,
) -> ProcessingJob | None:
    statement = (
        select(ProcessingJob)
        .join(WorkItem, ProcessingJob.work_item_id == WorkItem.id)
        .where(
            ProcessingJob.id == job_id,
            WorkItem.workspace_id == workspace_id,
        )
    )
    return db.execute(statement).scalar_one_or_none()

def get_jobs_for_work_item(
    db: Session,
    *,
    workspace_id: uuid.UUID,
    work_item_id: uuid.UUID,
) -> list[ProcessingJob]:
    statement = (
        select(ProcessingJob)
        .join(WorkItem, ProcessingJob.work_item_id == WorkItem.id)
        .where(
            ProcessingJob.work_item_id == work_item_id,
            WorkItem.workspace_id == workspace_id,
        )
        .order_by(ProcessingJob.created_at.desc())
    )
    return list(db.execute(statement).scalars().all())

def create_job(db: Session, *, obj_in: JobCreate) -> ProcessingJob:
    db_obj = ProcessingJob(work_item_id=obj_in.work_item_id)
    db.add(db_obj)
    db.commit()
    db.refresh(db_obj)
    return db_obj

def update_job(
    db: Session,
    *,
    db_obj: ProcessingJob,
    obj_in: JobUpdate,
) -> ProcessingJob:
    update_data = obj_in.model_dump(exclude_unset=True)
    if "metadata" in update_data:
        db_obj.execution_metadata = update_data.pop("metadata")
    for field, value in update_data.items():
        if hasattr(db_obj, field):
            setattr(db_obj, field, value)
    db.add(db_obj)
    db.commit()
    db.refresh(db_obj)
    return db_obj
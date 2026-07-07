"""
Data validation and serialization schemas (Pydantic v2) for Processing Jobs.

Enforces parameter boundaries on state progression, monitoring percentages, 
and encapsulates runtime error or stage tracking metadata.
"""

import uuid
from datetime import datetime
from enum import Enum
from typing import Any, Union
from pydantic import BaseModel, Field


class JobStatus(str, Enum):
    """
    Standard status states of an asynchronous processing job execution run.
    """
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class JobBase(BaseModel):
    """
    Base structural representation of properties common across Processing Jobs.
    """
    progress: int = Field(
        0, 
        ge=0, 
        le=100, 
        description="Core pipeline execution progress percentage."
    )
    status: JobStatus = Field(
        JobStatus.PENDING, 
        description="Active state of the processing run."
    )
    retry_count: int = Field(
        0, 
        ge=0, 
        description="Schedules automated execution retries under transient faults."
    )
    error_message: Union[str, None] = Field(
        None,
        max_length=5000,
        description="System error log or traceback context."
    )
    execution_metadata: Union[dict[str, Any], None] = Field(
        None,
        description="Dynamic dictionary storing granular performance indicators."
    )


class JobCreate(BaseModel):
    """
    Validation schema used to initiate a new background processing run context.
    """
    work_item_id: uuid.UUID = Field(
        ..., 
        description="Parent Work Item primary key identifier."
    )


class JobUpdate(BaseModel):
    """
    Validation schema used by workers to report incremental run metrics.
    """
    progress: Union[int, None] = Field(
        None, 
        ge=0, 
        le=100, 
        description="Updated execution progress percentage."
    )
    status: Union[JobStatus, None] = Field(
        None, 
        description="Transitional pipeline execution state."
    )
    retry_count: Union[int, None] = Field(
        None, 
        ge=0, 
        description="Updated execution retry count."
    )
    error_message: Union[str, None] = Field(
        None, 
        description="System error log or traceback context."
    )
    execution_metadata: Union[dict[str, Any], None] = Field(
        None,
        description="Granular run stage metadata dictionary."
    )


class JobResponse(JobBase):
    """
    Serialization schema returning structured operational metrics to client dashboards.
    """
    id: uuid.UUID
    work_item_id: uuid.UUID
    created_at: datetime
    updated_at: datetime

    model_config = {
        "from_attributes": True  # Maps seamlessly from SQLAlchemy 2.0 ORM objects
    }
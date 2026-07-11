"""
Data validation and serialization schemas (Pydantic v2) for Work Items.

Enforces parameter boundaries on file registrations, status updates, 
and maps standardized responses returned by the FlowPilot AI document queue.
"""

import uuid
from datetime import datetime
from enum import Enum
from typing import Any, Union
from pydantic import BaseModel, Field


class WorkItemStatus(str, Enum):
    """
    Standard status states of the asynchronous document processing pipeline.
    """
    QUEUED = "QUEUED"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class WorkItemBase(BaseModel):
    """
    Base structural representation of metadata properties common across Work Items.
    """
    original_filename: str = Field(
        ..., 
        max_length=255, 
        description="Original uploaded human-readable filename."
    )
    stored_filename: str = Field(
        ..., 
        max_length=255, 
        description="Unique system collision-safe stored filename."
    )
    file_type: str = Field(
        ..., 
        max_length=100, 
        description="MIME-type format identifier of the uploaded document."
    )
    file_size: int = Field(
        ..., 
        gt=0,
        le=104857600,  # 100 MB
        description="File size in bytes."
    )


class WorkItemCreate(WorkItemBase):
    """
    Validation schema used to initiate a new Work Item within the tracking system.
    """
    pass


class WorkItemUpdate(BaseModel):
    """
    Validation schema used to modify active Work Item status or store AI extraction output.
    """
    status: Union[WorkItemStatus, None] = Field(
        None, 
        description="Active processing pipeline status."
    )
    summary: Union[str, None] = Field(
        None, 
        description="AI-generated text summary of the document."
    )
    extracted_entities: Union[dict[str, Any], None] = Field(
        None, 
        description="AI-extracted metadata entities mapped as structured JSON."
    )


class WorkItemResponse(WorkItemBase):
    """
    Serialization schema returning structured Work Item data to client layers.
    """
    id: uuid.UUID
    status: WorkItemStatus
    summary: Union[str, None] = None
    extracted_entities: Union[dict[str, Any], None] = None
    user_id: uuid.UUID
    created_at: datetime
    updated_at: datetime

    model_config = {
        "from_attributes": True  # Enables direct mapping from SQLAlchemy 2.0 ORM objects
    }

class WorkItemListResponse(BaseModel):
    """
    Paginated list of Work Items returned to the frontend.
    """

    items: list[WorkItemResponse]

    page: int

    pageSize: int

    totalItems: int

    totalPages: int
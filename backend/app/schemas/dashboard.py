"""
Dashboard response schemas for FlowPilot AI.

Defines the response models returned by the Dashboard API.
"""

from pydantic import BaseModel, ConfigDict


class ProcessingStatus(BaseModel):
    """
    Current processing queue statistics.
    """

    queued: int
    processing: int
    total: int

    model_config = ConfigDict(from_attributes=True)


class DocumentTypeDistribution(BaseModel):
    """
    Document type distribution.
    """

    document_type: str
    count: int
    percentage: float

    model_config = ConfigDict(from_attributes=True)


class DashboardActivity(BaseModel):
    """
    Recent dashboard activity.
    """

    id: str
    event_type: str
    description: str
    timestamp: str
    work_item_id: str | None = None

    model_config = ConfigDict(from_attributes=True)


class DashboardOverviewResponse(BaseModel):
    """
    Complete dashboard overview response.
    """

    total_work_items: int

    processed_today: int

    processing_status: ProcessingStatus

    failed_count: int

    # F-161: completed / (completed + failed) as a percentage; None until a document finishes.
    automation_success_rate: float | None = None

    # File formats (PDF, PNG, ...) over every document.
    document_type_distribution: list[DocumentTypeDistribution]

    # Phase 1: what the documents are, from the classifier's label, over the classified ones.
    classification_distribution: list[DocumentTypeDistribution] = []

    recent_activity: list[DashboardActivity]

    model_config = ConfigDict(from_attributes=True)

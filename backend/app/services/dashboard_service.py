"""
Dashboard service for FlowPilot AI.

Contains the business logic responsible for building the
dashboard overview returned to the frontend.
"""

import uuid

from sqlalchemy.orm import Session

from app.crud.work_item import (
    count_work_items_for_user,
    count_processed_today_for_user,
    get_document_type_distribution,
    get_recent_work_items,
    get_processing_status,
    get_completion_statistics,
)

from app.schemas.dashboard import (
    DashboardOverviewResponse,
    DashboardActivity,
    DocumentTypeDistribution,
    ProcessingStatus,
)


def get_dashboard_overview(
    db: Session,
    user_id: uuid.UUID,
) -> DashboardOverviewResponse:
    """
    Returns dashboard analytics.
    """

    # ---------------------------------------------------------
    # Total Documents
    # ---------------------------------------------------------

    total_documents = count_work_items_for_user(
        db,
        user_id=user_id,
    )

    # ---------------------------------------------------------
    # Processed Today
    # ---------------------------------------------------------

    processed_today = count_processed_today_for_user(
        db,
        user_id=user_id,
    )

    # ---------------------------------------------------------
    # Document Distribution
    # ---------------------------------------------------------

    distribution = get_document_type_distribution(
        db,
        user_id=user_id,
    )

    document_distribution: list[DocumentTypeDistribution] = []

    for file_type, count in distribution:

        percentage = (
            (count / total_documents) * 100
            if total_documents > 0
            else 0
        )

        document_distribution.append(
            DocumentTypeDistribution(
                document_type=file_type.replace("application/", "").upper(),
                count=count,
                percentage=round(percentage, 1),
            )
        )

    # ---------------------------------------------------------
    # Recent Activity
    # ---------------------------------------------------------

    recent_work_items = get_recent_work_items(
        db,
        user_id=user_id,
    )

    recent_activity = []

    for work_item in recent_work_items:

        if work_item.status == "COMPLETED":
            event = "PROCESS_COMPLETED"

        elif work_item.status == "FAILED":
            event = "PROCESS_FAILED"

        elif work_item.status == "PROCESSING":
            event = "PROCESS_STARTED"

        else:
            event = "PROCESS_STARTED"

        recent_activity.append(
            DashboardActivity(
                id=str(work_item.id),
                event_type=event,
                description=work_item.original_filename,
                timestamp=work_item.updated_at.isoformat(),
                work_item_id=str(work_item.id),
            )
        )

    # ---------------------------------------------------------
    # Processing Status
    # ---------------------------------------------------------

    queued, processing = get_processing_status(
        db,
        user_id=user_id,
    )

    processing_status = ProcessingStatus(
        queued=queued,
        processing=processing,
        total=queued + processing,
    )

    # ---------------------------------------------------------
    # Success Rate
    # ---------------------------------------------------------

    completed, failed = get_completion_statistics(
        db,
        user_id=user_id,
    )

    total_finished = completed + failed

    if total_finished == 0:
        success_rate = 100.0
    else:
        success_rate = round(
            (completed / total_finished) * 100,
            1,
        )

    # ---------------------------------------------------------
    # Dashboard Response
    # ---------------------------------------------------------

    return DashboardOverviewResponse(
        total_work_items=total_documents,

        processed_today=processed_today,

        processing_status=processing_status,

        failed_count=failed,

        automation_success_rate=success_rate,

        document_type_distribution=document_distribution,

        recent_activity=recent_activity,
    )
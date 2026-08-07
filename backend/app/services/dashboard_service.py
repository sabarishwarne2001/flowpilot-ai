"""
Dashboard analytics for one workspace.

The dashboard describes a workspace, not a person: two members of the same
workspace see the same totals. Before ARCH-02 it was keyed on user_id and
therefore showed each user a private slice, which is the behaviour the
tenancy model replaces.
"""

import uuid

from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.crud.work_item import (
    count_work_items,
    count_completed_today,
    get_document_type_distribution,
    get_recent_work_items,
    get_processing_status,
    get_completion_statistics,
)
from app.schemas.dashboard import (
    DashboardActivity,
    DashboardOverviewResponse,
    DocumentTypeDistribution,
    ProcessingStatus,
)
from app.schemas.work_item import WorkItemStatus


_STATUS_EVENTS = {
    WorkItemStatus.COMPLETED: "PROCESS_COMPLETED",
    WorkItemStatus.FAILED: "PROCESS_FAILED",
    WorkItemStatus.PROCESSING: "PROCESS_STARTED",
}


def get_dashboard_overview(
    db: Session,
    context: TenantContext,
) -> DashboardOverviewResponse:
    """
    Builds the workspace overview.

    Takes the context rather than a bare workspace_id: it is request-path, and
    accepting the context means a caller cannot supply a workspace the actor
    was never authorised for. deps.get_workspace_context already resolved and
    verified that.
    """
    workspace_id = context.workspace_id

    total_documents = count_work_items(db, workspace_id=workspace_id)
    processed_today = count_completed_today(db, workspace_id=workspace_id)

    document_distribution = [
        DocumentTypeDistribution(
            document_type=file_type.replace("application/", "").upper(),
            count=count,
            percentage=round((count / total_documents) * 100, 1)
            if total_documents else 0,
        )
        for file_type, count in get_document_type_distribution(
            db, workspace_id=workspace_id
        )
    ]

    recent_activity = [
        DashboardActivity(
            id=str(item.id),
            event_type=_STATUS_EVENTS.get(item.status, "PROCESS_STARTED"),
            description=item.original_filename,
            timestamp=item.updated_at.isoformat(),
            work_item_id=str(item.id),
        )
        for item in get_recent_work_items(db, workspace_id=workspace_id)
    ]

    queued, processing = get_processing_status(db, workspace_id=workspace_id)
    completed, failed = get_completion_statistics(db, workspace_id=workspace_id)

    finished = completed + failed
    success_rate = 100.0 if finished == 0 else round(completed / finished * 100, 1)

    return DashboardOverviewResponse(
        total_work_items=total_documents,
        processed_today=processed_today,
        processing_status=ProcessingStatus(
            queued=queued, processing=processing, total=queued + processing
        ),
        failed_count=failed,
        automation_success_rate=success_rate,
        document_type_distribution=document_distribution,
        recent_activity=recent_activity,
    )
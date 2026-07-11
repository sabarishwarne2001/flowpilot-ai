"""
Dashboard API endpoints.

Provides analytics and overview information for the
FlowPilot dashboard.
"""

from typing import Any
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.models.user import User
from app.api import deps
from app.schemas.dashboard import DashboardOverviewResponse
from app.services.dashboard_service import get_dashboard_overview

router = APIRouter(tags=["Dashboard"])


@router.get(
    "/overview",
    response_model=DashboardOverviewResponse,
)
async def dashboard_overview(
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
) -> Any:
    """
    Returns dashboard overview metrics.
    """

    return get_dashboard_overview(
        db=db,
        user_id=current_user.id,
    )
"""
Dashboard API endpoints.
"""

from typing import Any
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
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
    context: deps.TenantContext = Depends(deps.RequireWorkspaceViewer),
) -> Any:
    return get_dashboard_overview(
        db=db,
        context=context,
    )

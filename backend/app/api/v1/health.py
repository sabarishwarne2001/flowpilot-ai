"""
System observability router for FlowPilot AI.

Provides diagnostic health status information describing the environment, 
build versions, and system timestamps.
"""

from datetime import datetime, UTC
from fastapi import APIRouter
from app.core.config import settings

router = APIRouter()

@router.get("", response_model_exclude_none=True)
async def get_health() -> dict[str, str]:
    """
    Performs high-speed diagnostics of the application running instance.
    Returns status indicators and current operational environment metadata.
    """
    return {
        "status": "healthy",
        "service": settings.PROJECT_NAME,
        "version": settings.APP_VERSION,
        "environment": settings.ENVIRONMENT,
        "timestamp": datetime.now(UTC).isoformat()
    }
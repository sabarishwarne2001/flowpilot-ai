"""
Centralized API v1 Routing gateway for FlowPilot AI.

Aggregates independent feature-subrouters (e.g., health checking, authentication, 
document queues, automation rules, notifications, and AI assistant chats) and 
exposes them under validated version pathways.
"""

from fastapi import APIRouter
from app.api.v1.health import router as health_router
from app.api.v1.auth import router as auth_router
from app.api.v1.work_items import router as work_items_router
from app.api.v1.automation import router as automation_router
from app.api.v1.notifications import router as notifications_router
from app.api.v1.assistant import router as assistant_router
from app.api.v1.dashboard import router as dashboard_router
from app.api.v1.email_settings import router as email_settings_router

api_router = APIRouter()

api_router.include_router(health_router, prefix="/health", tags=["Health"])

api_router.include_router(auth_router, prefix="/auth", tags=["Authentication"])

api_router.include_router(
    dashboard_router,
    prefix="/dashboard",
    tags=["Dashboard"],
)

api_router.include_router(work_items_router, prefix="/work-items", tags=["Work Items"])

api_router.include_router(automation_router, prefix="/automation", tags=["Automation"])

api_router.include_router(notifications_router, prefix="/notifications", tags=["Notifications"])

api_router.include_router(assistant_router, prefix="/assistant", tags=["AI Assistant"])

api_router.include_router(
    email_settings_router,
    prefix="/email-settings",
    tags=["Email Settings"],
)
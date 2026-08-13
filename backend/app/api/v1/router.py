"""
Centralized API v1 routing gateway for FlowPilot AI.
"""

from fastapi import APIRouter

from app.api.v1 import (
    ai_settings,
    assistant,
    audit_logs,
    automation,
    avatar,
    dashboard,
    document_settings,
    email_change,
    email_settings,
    me,
    notifications,
    organization_email_settings,
    organization_notifications,
    organizations,
    ownership_transfers,
    upload,
    work_items,
    workspaces,
)
from app.api.v1.auth import router as auth_router
from app.api.v1.health import router as health_router
from app.api.v1.organization_invitations import router as organization_invitation_router

api_router = APIRouter()

WORKSPACE_PREFIX = "/workspaces/{workspace_id}"


# ============================================================================
# Tenancy — routers declare their own full paths
# ============================================================================

api_router.include_router(organizations.router)
api_router.include_router(organization_email_settings.router)
api_router.include_router(organization_notifications.router)
api_router.include_router(audit_logs.router)
api_router.include_router(me.router)
api_router.include_router(avatar.router)
api_router.include_router(email_change.router)
api_router.include_router(workspaces.router)
api_router.include_router(organization_invitation_router)
api_router.include_router(ownership_transfers.router)
api_router.include_router(upload.logo_router)


# ============================================================================
# Global
# ============================================================================

api_router.include_router(health_router, prefix="/health", tags=["Health"])
api_router.include_router(auth_router, prefix="/auth", tags=["Authentication"])


# ============================================================================
# Workspace-scoped
# ============================================================================

_SCOPED = (
    (work_items.router,        "/work-items",         "Work Items"),
    (dashboard.router,         "/dashboard",          "Dashboard"),
    (assistant.router,         "/assistant",          "AI Assistant"),
    (automation.router,        "/automation",         "Automation"),
    (notifications.router,     "/notifications",      "Notifications"),
    (ai_settings.router,       "/ai-settings",        "AI Settings"),
    (email_settings.router,    "/email-settings",     "Email Settings"),
    (document_settings.router, "/document-settings",  "Document Settings"),
    (upload.router,            "/upload",             "Upload"),
)

for _router, _suffix, _tag in _SCOPED:
    api_router.include_router(
        _router,
        prefix=f"{WORKSPACE_PREFIX}{_suffix}",
        tags=[_tag],
    )
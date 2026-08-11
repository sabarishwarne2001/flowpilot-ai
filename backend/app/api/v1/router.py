"""
Centralized API v1 routing gateway for FlowPilot AI.

Two registration families since ARCH-02:

  1. Tenancy routers (organizations, me, workspaces, invitations) declare their
     full paths and register with NO prefix. Their shapes are fixed by the
     ARCH-01 contract, so burying a segment in the prefix would make a route's
     real address unreadable from its own source file.

  2. Workspace-scoped routers register under WORKSPACE_PREFIX. FastAPI resolves
     path parameters declared in a prefix, so RequireWorkspaceRole reads
     workspace_id without a single route decorator being edited.

The third family — "global and not-yet-scoped" — is gone. work_items,
automation, notifications, assistant, and dashboard moved under tenant
addressing in ARCH-02 when their tables gained workspace_id. Their previous
flat routes were REMOVED rather than redirected: an alias would keep the
unscoped handler reachable, and the unscoped handler is the vulnerability.

Remaining genuinely global routers: health, auth, and upload. None reads
tenant-scoped tables.
"""

from fastapi import APIRouter

from app.api.v1 import (
    ai_settings,
    assistant,
    automation,
    dashboard,
    document_settings,
    email_settings,
    me,
    notifications,
    organizations,
    ownership_transfers,
    upload,
    work_items,
    workspaces,
)
from app.api.v1.auth import router as auth_router
from app.api.v1.health import router as health_router
# Swap the legacy router for the new organization-scoped router (§D7.2 / §3.6)
from app.api.v1.organization_invitations import router as organization_invitation_router

api_router = APIRouter()

#: Prefix carrying the workspace path parameter that RequireWorkspaceRole
#: resolves. Declared once so every workspace-scoped mount stays consistent.
WORKSPACE_PREFIX = "/workspaces/{workspace_id}"


# ============================================================================
# Tenancy — routers declare their own full paths
# ============================================================================

api_router.include_router(organizations.router)
api_router.include_router(me.router)
api_router.include_router(workspaces.router)
# Mount the new organization invitation router with no prefix (handles /invitations/*)
api_router.include_router(organization_invitation_router)
# ARCH-05 Step 7 — ownership transfer. Declares its own full paths
# (/organizations/{id}/ownership-transfers/* and /me/ownership-transfers),
# so it registers with no prefix like every other tenancy router above.
api_router.include_router(ownership_transfers.router)


# ============================================================================
# Global
# ============================================================================

api_router.include_router(health_router, prefix="/health", tags=["Health"])
api_router.include_router(auth_router, prefix="/auth", tags=["Authentication"])
api_router.include_router(upload.router)


# ============================================================================
# Workspace-scoped
#
# The workspace segment lives in the prefix rather than in each decorator, so
# no route definition carries it. Every handler beneath these mounts takes a
# TenantContext and derives its scope from context.workspace_id.
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
)

for _router, _suffix, _tag in _SCOPED:
    api_router.include_router(
        _router,
        prefix=f"{WORKSPACE_PREFIX}{_suffix}",
        tags=[_tag],
    )
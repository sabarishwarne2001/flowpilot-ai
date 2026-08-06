"""
Centralized API v1 Routing gateway for FlowPilot AI.

Aggregates independent feature-subrouters and exposes them under validated
version pathways.

Three registration families, deliberately distinct:

  1. Tenancy routers (organizations, me, workspaces, invitations) declare their
     full paths and register with NO prefix. Their shapes are fixed by the
     ARCH-01 contract, so burying a segment in the prefix would make a route's
     real address unreadable from its own source file.

  2. Workspace-scoped settings register under a prefix carrying the workspace
     path parameter. FastAPI resolves path parameters declared in a prefix, so
     RequireWorkspaceRole can read workspace_id without a single route
     decorator being edited.

  3. Global and not-yet-scoped routers keep their original flat prefixes.

app/api/v1/workspace.py was deleted in ARCH-01. It resolved "the user's
workspace" from a single active membership, an assumption that returned HTTP
500 for any account holding two. Its responsibilities are now split across
workspaces.py (settings and members), organizations.py (tenant management), and
invitations.py (the invitation lifecycle, with authenticated acceptance).

Known interim gap, closing in ARCH-02: work_items, automation, notifications,
assistant, and dashboard are not workspace-scoped. Their tables are keyed on
user_id, so a workspace guard would authorize a query that ignores the
workspace. They are re-scoped when those tables gain workspace_id, and their
route prefixes move under /workspaces/{workspace_id} at the same time.
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
from app.api.v1 import ai_settings
from app.api.v1 import upload
from app.api.v1 import document_settings
from app.api.v1 import organizations
from app.api.v1 import me
from app.api.v1 import workspaces
from app.api.v1 import invitations

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

api_router.include_router(invitations.router)


# ============================================================================
# Global
# ============================================================================

api_router.include_router(health_router, prefix="/health", tags=["Health"])

api_router.include_router(auth_router, prefix="/auth", tags=["Authentication"])

api_router.include_router(upload.router)


# ============================================================================
# Workspace-scoped settings
#
# The workspace segment lives in the prefix rather than in each decorator, so
# no route definition changed when these moved under tenant addressing.
# ============================================================================

api_router.include_router(
    ai_settings.router,
    prefix=f"{WORKSPACE_PREFIX}/ai-settings",
    tags=["AI Settings"],
)

api_router.include_router(
    email_settings_router,
    prefix=f"{WORKSPACE_PREFIX}/email-settings",
    tags=["Email Settings"],
)

api_router.include_router(
    document_settings.router,
    prefix=f"{WORKSPACE_PREFIX}/document-settings",
    tags=["Document Settings"],
)


# ============================================================================
# Not yet workspace-scoped — see the module docstring. ARCH-02.
# ============================================================================

api_router.include_router(
    dashboard_router, prefix="/dashboard", tags=["Dashboard"]
)

api_router.include_router(
    work_items_router, prefix="/work-items", tags=["Work Items"]
)

api_router.include_router(
    automation_router, prefix="/automation", tags=["Automation"]
)

api_router.include_router(
    notifications_router, prefix="/notifications", tags=["Notifications"]
)

api_router.include_router(
    assistant_router, prefix="/assistant", tags=["AI Assistant"]
)
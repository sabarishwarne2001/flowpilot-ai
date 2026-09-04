"""Centralized API v1 routing gateway for FlowPilot AI."""

from fastapi import APIRouter

from app.api.v1 import (
    ai_settings,
    api_keys,
    assistant,
    assistant_stream,
    audit_logs,
    automation,
    avatar,
    byok,
    compliance,
    custom_domains,
    dashboard,
    developer,
    document_settings,
    email_change,
    email_settings,
    identity_admin,
    me,
    notifications,
    organization_email_settings,
    organization_notifications,
    organizations,
    ownership_transfers,
    slos,
    tenant_branding,
    upload,
    usage,
    verifications,
    warehouse_sync,
    work_items,
    workspaces,
)
from app.api.v1.admin import cogs as admin_cogs
from app.api.v1.public import router as public_gateway_router
from app.api.v1.auth import router as auth_router
from app.api.v1.health import router as health_router
from app.api.v1.organization_invitations import router as organization_invitation_router
from app.api.v1.saml import oidc_router, saml_router, sso_router

api_router = APIRouter()

WORKSPACE_PREFIX = "/workspaces/{workspace_id}"

# Tenancy — routers declare their own full paths
api_router.include_router(organizations.router)
api_router.include_router(organization_email_settings.router)
api_router.include_router(organization_notifications.router)
api_router.include_router(audit_logs.router)
api_router.include_router(api_keys.router)
api_router.include_router(me.router)
api_router.include_router(avatar.router)
api_router.include_router(email_change.router)
api_router.include_router(workspaces.router)
api_router.include_router(organization_invitation_router)
api_router.include_router(ownership_transfers.router)
api_router.include_router(upload.logo_router)
api_router.include_router(usage.router)
api_router.include_router(slos.router)
api_router.include_router(compliance.router)
api_router.include_router(developer.router)  # ARCH-21 Tenant Developer Portal
api_router.include_router(public_gateway_router)  # ARCH-21 Public Developer Gateway
api_router.include_router(byok.router)  # ARCH-22 Enterprise BYOK & Model Routing

# ARCH-25 White-label. Two routers rather than one because the role
# boundary differs: every domain write is OWNER-gated (a vanity hostname
# is an authentication-adjacent control), while branding writes are
# ADMIN-gated. Mounting them together would invite one shared dependency.
#
# tenant_branding.public_router carries the ONE unauthenticated route in
# this phase and is mounted separately so that adding an endpoint to it
# is a visible act rather than an accident of file position.
api_router.include_router(custom_domains.router)
api_router.include_router(tenant_branding.router)
api_router.include_router(tenant_branding.public_router)

# ARCH-26 Enterprise Analytics, BI Egress & Warehouse Sync.
#
# One router. Unlike ARCH-25's domain/branding split, the role boundary here
# is uniform — reads ADMIN, writes OWNER — and is carried by per-endpoint
# dependencies rather than by which router an endpoint landed in.
api_router.include_router(warehouse_sync.router)

api_router.include_router(admin_cogs.router)
api_router.include_router(identity_admin.router)

# Global & Identity Federation
api_router.include_router(health_router, prefix="/health", tags=["Health"])
api_router.include_router(auth_router, prefix="/auth", tags=["Authentication"])
api_router.include_router(saml_router)
api_router.include_router(sso_router)
api_router.include_router(oidc_router)

# Workspace-scoped
_SCOPED = (
    (work_items.router,        "/work-items",         "Work Items"),
    (dashboard.router,         "/dashboard",          "Dashboard"),
    (assistant.router,         "/assistant",          "AI Assistant"),
    (assistant_stream.router,  "/assistant",          "AI Assistant"),
    (automation.router,        "/automation",         "Automation"),
    (notifications.router,     "/notifications",      "Notifications"),
    (ai_settings.router,       "/ai-settings",        "AI Settings"),
    (email_settings.router,    "/email-settings",     "Email Settings"),
    (document_settings.router, "/document-settings",  "Document Settings"),
    (upload.router,            "/upload",             "Upload"),
    (usage.workspace_router,   "/usage",              "Usage"),
    (verifications.router,     "/verifications",      "Verifications"),
)

for _router, _suffix, _tag in _SCOPED:
    api_router.include_router(
        _router,
        prefix=f"{WORKSPACE_PREFIX}{_suffix}",
        tags=[_tag],
    )

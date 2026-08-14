"""
Route audit classification helper (ARCH-08 Step 4).

Maps endpoint routes and HTTP verbs to AuditResourceType and AuditAction pairs
for automated guard-level denial capture.
"""

from __future__ import annotations

from typing import Any
from fastapi import Request

from app.models.audit_log import AuditAction, AuditResourceType

_METHOD_ACTIONS: dict[str, AuditAction] = {
    "GET": AuditAction.ACCESSED,
    "HEAD": AuditAction.ACCESSED,
    "POST": AuditAction.CREATED,
    "PUT": AuditAction.UPDATED,
    "PATCH": AuditAction.UPDATED,
    "DELETE": AuditAction.DELETED,
}

ROUTE_AUDIT_OVERRIDES: dict[tuple[str, str], tuple[AuditResourceType, AuditAction]] = {
    ("PATCH", "/organizations/{organization_id}/members/{user_id}/role"): (
        AuditResourceType.MEMBERSHIP,
        AuditAction.ROLE_CHANGED,
    ),
    ("POST", "/organizations/{organization_id}/ownership-transfers"): (
        AuditResourceType.OWNERSHIP_TRANSFER,
        AuditAction.CREATED,
    ),
    ("POST", "/organizations/{organization_id}/invitations/{invitation_id}/resend"): (
        AuditResourceType.INVITATION,
        AuditAction.CREATED,
    ),
}


def resolve_route_audit(
    request: Request, *, default_resource: AuditResourceType
) -> tuple[AuditResourceType, AuditAction]:
    route = request.scope.get("route")
    path = getattr(route, "path", "") if route else ""
    override = ROUTE_AUDIT_OVERRIDES.get((request.method, path))
    if override is not None:
        return override
    return default_resource, _METHOD_ACTIONS.get(request.method, AuditAction.ACCESSED)
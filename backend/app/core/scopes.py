"""Named scope taxonomy and effective permission calculation (ARCH-08 §B.2, §9.3)."""

from __future__ import annotations

from enum import Enum
from typing import Any, Mapping

from app.models.api_key import ApiKey
from app.models.organization import MembershipStatus, OrganizationMember, OrganizationRole


class ApiKeyScope(str, Enum):
    ORGANIZATIONS_READ = "organizations:read"
    WORKSPACES_READ = "workspaces:read"
    WORKSPACES_WRITE = "workspaces:write"
    MEMBERS_READ = "members:read"
    WORK_ITEMS_READ = "work_items:read"
    WORK_ITEMS_WRITE = "work_items:write"
    AUDIT_LOGS_READ = "audit_logs:read"
    FILES_READ = "files:read"
    FILES_WRITE = "files:write"


PERMANENTLY_EXCLUDED_SCOPES = frozenset({
    "ownership:*",
    "members:write",
    "api_keys:write",
    "settings:write",
})

SCOPES_BY_ROLE: dict[OrganizationRole, frozenset[ApiKeyScope]] = {
    OrganizationRole.OWNER: frozenset(ApiKeyScope),
    OrganizationRole.ADMIN: frozenset(ApiKeyScope),
    OrganizationRole.MEMBER: frozenset({
        ApiKeyScope.ORGANIZATIONS_READ,
        ApiKeyScope.WORKSPACES_READ,
        ApiKeyScope.WORK_ITEMS_READ,
        ApiKeyScope.WORK_ITEMS_WRITE,
        ApiKeyScope.FILES_READ,
        ApiKeyScope.FILES_WRITE,
    }),
    OrganizationRole.BILLING: frozenset({
        ApiKeyScope.ORGANIZATIONS_READ,
    }),
}

# Route template mapping for scope enforcement
ROUTE_SCOPE_MAP: dict[tuple[str, str], ApiKeyScope] = {
    ("GET", "/organizations/{organization_id}"): ApiKeyScope.ORGANIZATIONS_READ,
    ("GET", "/organizations/{organization_id}/workspaces"): ApiKeyScope.WORKSPACES_READ,
    ("POST", "/organizations/{organization_id}/workspaces"): ApiKeyScope.WORKSPACES_WRITE,
    ("GET", "/organizations/{organization_id}/members"): ApiKeyScope.MEMBERS_READ,
    ("GET", "/organizations/{organization_id}/audit-logs"): ApiKeyScope.AUDIT_LOGS_READ,
    ("GET", "/organizations/{organization_id}/audit-logs/export"): ApiKeyScope.AUDIT_LOGS_READ,
    ("GET", "/workspaces/{workspace_id}/work-items"): ApiKeyScope.WORK_ITEMS_READ,
    ("POST", "/workspaces/{workspace_id}/work-items"): ApiKeyScope.WORK_ITEMS_WRITE,
    ("GET", "/workspaces/{workspace_id}/logo"): ApiKeyScope.FILES_READ,
    ("POST", "/logo"): ApiKeyScope.FILES_WRITE,
    ("DELETE", "/logo"): ApiKeyScope.FILES_WRITE,
}


def effective_scopes(key: ApiKey, membership: OrganizationMember) -> frozenset[ApiKeyScope]:
    if membership.status is not MembershipStatus.ACTIVE:
        return frozenset()

    granted = {
        ApiKeyScope(s) for s in key.scopes
        if s not in PERMANENTLY_EXCLUDED_SCOPES and any(s == member.value for member in ApiKeyScope)
    }
    allowed_by_role = SCOPES_BY_ROLE.get(membership.role, frozenset())
    return granted & allowed_by_role
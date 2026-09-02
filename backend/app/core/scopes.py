"""Named scope taxonomy and effective permission calculation (ARCH-08, ARCH-09, ARCH-15, ARCH-21)."""

from __future__ import annotations

from enum import Enum
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
    WEBHOOKS_READ = "webhooks:read"
    WEBHOOKS_WRITE = "webhooks:write"
    WEBHOOKS_ADMIN = "webhooks:admin"
    BILLING_READ = "billing:read"
    # --- ARCH-21 Public Gateway Scopes ---
    PUBLIC_DOCUMENTS_READ = "public_documents:read"
    PUBLIC_QUERY_WRITE = "public_query:write"
    PUBLIC_WORKFLOWS_READ = "public_workflows:read"
    PUBLIC_WORKFLOWS_WRITE = "public_workflows:write"


PERMANENTLY_EXCLUDED_SCOPES = frozenset({
    "ownership:*",
    "members:write",
    "api_keys:write",
    "settings:write",
    "billing:write",
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
        ApiKeyScope.PUBLIC_DOCUMENTS_READ,
        ApiKeyScope.PUBLIC_QUERY_WRITE,
        ApiKeyScope.PUBLIC_WORKFLOWS_READ,
    }),
    OrganizationRole.BILLING: frozenset({
        ApiKeyScope.ORGANIZATIONS_READ,
        ApiKeyScope.BILLING_READ,
    }),
}

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
    ("POST", "/organizations/{organization_id}/webhooks/endpoints"): ApiKeyScope.WEBHOOKS_WRITE,
    ("GET", "/organizations/{organization_id}/webhooks/endpoints"): ApiKeyScope.WEBHOOKS_READ,
    ("GET", "/organizations/{organization_id}/webhooks/endpoints/{endpoint_id}"): ApiKeyScope.WEBHOOKS_READ,
    ("PATCH", "/organizations/{organization_id}/webhooks/endpoints/{endpoint_id}"): ApiKeyScope.WEBHOOKS_WRITE,
    ("DELETE", "/organizations/{organization_id}/webhooks/endpoints/{endpoint_id}"): ApiKeyScope.WEBHOOKS_WRITE,
    ("POST", "/organizations/{organization_id}/webhooks/endpoints/{endpoint_id}/rotate-secret"): ApiKeyScope.WEBHOOKS_ADMIN,
    ("GET", "/organizations/{organization_id}/webhooks/endpoints/{endpoint_id}/deliveries"): ApiKeyScope.WEBHOOKS_READ,
    ("GET", "/organizations/{organization_id}/webhooks/deliveries/{delivery_id}/attempts"): ApiKeyScope.WEBHOOKS_READ,
    ("POST", "/organizations/{organization_id}/webhooks/deliveries/{delivery_id}/redeliver"): ApiKeyScope.WEBHOOKS_WRITE,
    ("GET", "/organizations/{organization_id}/invoices"): ApiKeyScope.BILLING_READ,
    ("GET", "/organizations/{organization_id}/invoices/{invoice_id}"): ApiKeyScope.BILLING_READ,
    ("GET", "/organizations/{organization_id}/invoices/{invoice_id}/reproduction"): ApiKeyScope.BILLING_READ,
    ("GET", "/organizations/{organization_id}/billing/subscription"): ApiKeyScope.BILLING_READ,
    ("GET", "/organizations/{organization_id}/billing/access"): ApiKeyScope.BILLING_READ,
    # --- ARCH-21 Public Gateway Routes ---
    ("GET", "/public/documents"): ApiKeyScope.PUBLIC_DOCUMENTS_READ,
    ("GET", "/public/documents/{work_item_id}"): ApiKeyScope.PUBLIC_DOCUMENTS_READ,
    ("POST", "/public/query"): ApiKeyScope.PUBLIC_QUERY_WRITE,
    ("GET", "/public/workflows"): ApiKeyScope.PUBLIC_WORKFLOWS_READ,
    ("POST", "/public/workflows/{rule_id}/trigger"): ApiKeyScope.PUBLIC_WORKFLOWS_WRITE,
}

PUBLIC_API_SCOPES: frozenset[ApiKeyScope] = frozenset({
    ApiKeyScope.PUBLIC_DOCUMENTS_READ,
    ApiKeyScope.PUBLIC_QUERY_WRITE,
    ApiKeyScope.PUBLIC_WORKFLOWS_READ,
    ApiKeyScope.PUBLIC_WORKFLOWS_WRITE,
})


def effective_scopes(key: ApiKey, membership: OrganizationMember) -> frozenset[ApiKeyScope]:
    if membership.status is not MembershipStatus.ACTIVE:
        return frozenset()

    granted = {
        ApiKeyScope(s) for s in key.scopes
        if s not in PERMANENTLY_EXCLUDED_SCOPES and any(s == member.value for member in ApiKeyScope)
    }
    allowed_by_role = SCOPES_BY_ROLE.get(membership.role, frozenset())
    return granted & allowed_by_role

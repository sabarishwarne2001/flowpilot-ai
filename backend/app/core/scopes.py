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
    # --- Helm (Enterprise: capability.helm_api, checked again in Helm's own gate) ---
    HELM_READ = "helm:read"
    HELM_WRITE = "helm:write"
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
        # Billing sees all of Helm's figures; what it may change in Helm it changes signed in.
        ApiKeyScope.HELM_READ,
    }),
}

#: Helm's routes (app/api/v1/helm.py) an API key may call: a GET reads (helm:read); a POST, PATCH or
#: DELETE needs helm:write, previews included, so a read-only key never sends Helm a body. A key may
#: feed Helm facts (cash, payments, bank statements and their lines) and ask it to work (a plan, a
#: mandate preview, the Time machine). It reaches them only on a plan with capability.helm_api
#: (`helm.gate.assert_api_access`, 402 otherwise).
_HELM = "/organizations/{organization_id}/helm"
_HELM_KEY_ROUTES: tuple[tuple[str, str], ...] = (
    ("GET", ""),
    ("GET", "/workspaces/{workspace_id}/card"),
    ("GET", "/library"),
    ("GET", "/books/{book_id}"),
    ("GET", "/books/{book_id}/settings"),
    ("POST", "/books/{book_id}/cash-positions"),
    ("GET", "/books/{book_id}/counterparties"),
    ("GET", "/books/{book_id}/cockpit"),
    ("POST", "/books/{book_id}/plan"),
    ("GET", "/books/{book_id}/mandates"),
    ("POST", "/books/{book_id}/mandates/preview"),
    ("GET", "/books/{book_id}/ledger"),
    ("GET", "/books/{book_id}/calendar"),
    ("GET", "/books/{book_id}/payment-run"),
    ("POST", "/books/{book_id}/invoices/{work_item_id}/paid"),
    ("GET", "/books/{book_id}/certificates"),
    ("GET", "/actions/{action_id}"),
    ("GET", "/certificates/{certificate_id}"),
    ("GET", "/certificates/{certificate_id}/download"),
    ("GET", "/books/{book_id}/bank-accounts"),
    ("POST", "/books/{book_id}/bank-accounts"),
    ("PATCH", "/books/{book_id}/bank-accounts/{account_id}"),
    ("POST", "/books/{book_id}/bank-statements/preview"),
    ("POST", "/books/{book_id}/bank-statements"),
    ("GET", "/books/{book_id}/bank-imports"),
    ("GET", "/books/{book_id}/bank-lines"),
    ("POST", "/bank-lines/{line_id}/match"),
    ("POST", "/bank-lines/{line_id}/ignore"),
    ("POST", "/bank-lines/{line_id}/reopen"),
    ("POST", "/backtests"),
    ("GET", "/books/{book_id}/backtests"),
    ("GET", "/backtests/{backtest_id}"),
    ("GET", "/backtests/{backtest_id}/pdf"),
    # A what-if saves nothing; like the mandate preview it is a POST, so it takes helm:write.
    ("POST", "/books/{book_id}/what-if"),
)

_HELM_DECISION = ("No API key scope covers approving, rejecting or undoing a Helm action. That is a person's "
                  "decision, and Helm records who made it: sign in to do it.")
_HELM_RULES = ("No API key scope covers changing Helm's rules or set-up (books, settings, counterparties, "
               "mandates, the sample company). An owner or an admin does that signed in.")
#: Helm's routes no API key may call, whatever its scopes, and the words it is refused with (403).
_HELM_PEOPLE_ONLY: dict[tuple[str, str], str] = {
    ("POST", "/books/{book_id}/plan/approve-all"): _HELM_DECISION,
    ("POST", "/actions/{action_id}/approve"): _HELM_DECISION,
    ("POST", "/actions/{action_id}/reject"): _HELM_DECISION,
    ("POST", "/actions/{action_id}/undo"): _HELM_DECISION,
    ("POST", "/books"): _HELM_RULES,
    ("PATCH", "/books/{book_id}/settings"): _HELM_RULES,
    ("PATCH", "/books/{book_id}/counterparties/{counterparty_id}"): _HELM_RULES,
    ("POST", "/books/{book_id}/mandates"): _HELM_RULES,
    ("POST", "/books/{book_id}/mandates/draft"): _HELM_RULES,
    ("PATCH", "/books/{book_id}/mandates/{mandate_id}"): _HELM_RULES,
    ("POST", "/books/{book_id}/mandates/{mandate_id}/activate"): _HELM_RULES,
    ("POST", "/books/{book_id}/mandates/{mandate_id}/pause"): _HELM_RULES,
    ("POST", "/books/{book_id}/mandates/{mandate_id}/archive"): _HELM_RULES,
    ("POST", "/sample"): _HELM_RULES,
    ("DELETE", "/sample"): _HELM_RULES,
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
    # Campaign session 2 (N-056): what the key screen promises. read = "read documents and their
    # extracted content"; write = "upload and modify documents".
    ("GET", "/workspaces/{workspace_id}/work-items/{work_item_id}"): ApiKeyScope.WORK_ITEMS_READ,
    ("GET", "/workspaces/{workspace_id}/work-items/{work_item_id}/content"): ApiKeyScope.WORK_ITEMS_READ,
    ("POST", "/workspaces/{workspace_id}/work-items/{work_item_id}/reprocess"): ApiKeyScope.WORK_ITEMS_WRITE,
    ("DELETE", "/workspaces/{workspace_id}/work-items/{work_item_id}"): ApiKeyScope.WORK_ITEMS_WRITE,
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
    # --- Helm (see _HELM_KEY_ROUTES) ---
    **{(method, f"{_HELM}{path}"): ApiKeyScope.HELM_READ if method == "GET" else ApiKeyScope.HELM_WRITE
       for method, path in _HELM_KEY_ROUTES},
}

#: Routes no API key may call whatever scopes it holds, each with the words it is refused with (403).
#: Default deny refuses them anyway (no scope names them); this says why, in plain words.
PEOPLE_ONLY_ROUTES: dict[tuple[str, str], str] = {
    (method, f"{_HELM}{path}"): words for (method, path), words in _HELM_PEOPLE_ONLY.items()
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

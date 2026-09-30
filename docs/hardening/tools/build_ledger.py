"""Build docs/hardening/COVERAGE.csv from the Phase 0 code extraction."""
import ast
import csv
import re
import sys
from pathlib import Path

REPO = Path(sys.argv[1])
SP = Path(sys.argv[2])
OUT = REPO / "docs/hardening/COVERAGE.csv"
BACKEND = REPO / "backend"

COLS = ["type", "id", "description", "plan_required", "roles_allowed", "test_file",
        "status", "last_result", "finding_ids"]
rows = []


def add(type_, id_, desc, plan="", roles="", test="", findings="", critical=""):
    if critical:
        desc = f"[CRITICAL:{critical}] {desc}"
    rows.append({"type": type_, "id": id_, "description": desc, "plan_required": plan,
                 "roles_allowed": roles, "test_file": test, "status": "untested",
                 "last_result": "", "finding_ids": findings})


# Capability -> lowest plan that grants it (scripts/seed_quota_tiers.py)
CAP_PLAN = {
    "developer_api": "developer", "outgoing_webhooks": "developer", "custom_branding": "developer",
    "custom_domain": "developer(addon.custom_domain)",
    "reconciliation": "business", "anomaly_radar": "business", "custom_email": "business",
    "extraction_memory": "business", "entity_graph": "business", "case_intelligence": "business",
    "table_intelligence": "business", "obligations": "business", "erp_posting": "business",
    "warehouse_sync": "business(addon.warehouse_sync)",
    "redaction": "enterprise", "semantic_assertions": "enterprise", "calibrated_autonomy": "enterprise",
    "enterprise_identity": "enterprise", "priority_slo": "enterprise",
    "universal_corroborator": "enterprise", "collaborative_review": "enterprise",
    "process_intelligence": "enterprise", "egress_lockdown": "enterprise",
}


def plan_for(cap):
    if not cap:
        return "any"
    key = cap.lower().replace("capability.", "").replace("_capability", "")
    return CAP_PLAN.get(key, cap)


WS_ROLES = "ws:VIEWER+ (all workspace members)"

# ------------------------------------------------------------------ pages
# (route, component, guard, plan capability, roles, critical journey)
P = "/:orgSlug/:workspaceSlug"
O = "/organizations/:orgSlug"
pages = [
    ("/login", "Login (PublicRoute+AuthLayout)", "", "anonymous", "auth"),
    ("/register", "Register (PublicRoute+AuthLayout)", "", "anonymous", "auth"),
    ("/verify-email", "VerifyEmail (public)", "", "anonymous", "auth"),
    ("/forgot-password", "ForgotPassword (public)", "", "anonymous", "auth"),
    ("/reset-password", "ResetPassword (public)", "", "anonymous", "auth"),
    ("/invitations/accept", "InvitationAcceptPage (public)", "", "anonymous/invitee", "team"),
    ("/confirm-email-change", "ConfirmEmailChange (public)", "", "anonymous", "auth"),
    ("/auth/sso/complete", "SsoComplete (AuthLayout; outside Public/PrivateRoute)", "", "anonymous", "auth"),
    ("/request/:token", "DocumentRequestUpload (public; single-use token is the credential)", "", "token holder", "upload"),
    ("/onboarding", "CreateOrganizationPage (PrivateRoute)", "", "any signed-in user", "auth"),
    ("/organizations/new", "CreateOrganizationPage (PrivateRoute)", "", "any signed-in user", ""),
    ("/workspaces", "WorkspacePicker (PrivateRoute)", "", "any signed-in user", ""),
    ("/no-access", "NoAccess (PrivateRoute)", "", "any signed-in user", ""),
    (f"{O}/workspaces/new", "CreateWorkspacePage (PrivateRoute)", "", "org member (server decides)", ""),
    (f"{O}", "OrganizationGuard index -> redirect to settings", "", "org member", ""),
    (f"{O}/settings", "OrganizationGeneral", "", "org:any", ""),
    (f"{O}/notifications", "OrganizationNotifications", "", "org:any", ""),
    (f"{O}/members", "OrganizationMembers", "", "org:OWNER,ADMIN (nav)", "team"),
    (f"{O}/email-settings", "OrganizationEmailSettings", "custom_email", "org:OWNER,ADMIN (nav)", ""),
    (f"{O}/service-levels", "OrganizationSLOs", "", "org:OWNER,ADMIN (nav)", ""),
    (f"{O}/compliance", "OrganizationCompliance (data governance)", "", "org:OWNER,ADMIN (nav); writes OWNER", "data-governance"),
    (f"{O}/developer", "OrganizationDeveloperPortal", "developer_api", "org:OWNER,ADMIN (nav)", "api-keys-webhooks"),
    (f"{O}/byok", "OrganizationBYOK", "", "org:OWNER,ADMIN (nav); writes OWNER", ""),
    (f"{O}/branding", "OrganizationBranding", "custom_branding", "org:OWNER,ADMIN (nav); domain ops OWNER", ""),
    (f"{O}/analytics", "OrganizationAnalytics (BI egress)", "warehouse_sync", "org:OWNER,ADMIN (nav); writes OWNER", ""),
    (f"{O}/marketplace", "MarketplaceCatalog", "", "org:OWNER,ADMIN (nav); install OWNER", ""),
    (f"{O}/autonomy", "AutonomySettings", "calibrated_autonomy", "org:OWNER,ADMIN (nav); writes OWNER", ""),
    (f"{O}/egress", "OrganizationEgress", "egress_lockdown", "org:OWNER,ADMIN (nav); writes OWNER", ""),
    (f"{O}/billing", "BillingHub (plans, invoices, seats, usage, spend limit)", "", "org:OWNER,BILLING (nav)", "billing"),
    (f"{O}/billing/return", "CheckoutReturn", "", "org:OWNER,BILLING", "billing"),
    (f"{O}/api-keys", "OrganizationApiKeys", "developer_api", "org:OWNER (nav)", "api-keys-webhooks"),
    (f"{O}/webhooks", "OrganizationWebhooks", "outgoing_webhooks", "org:OWNER (nav)", "api-keys-webhooks"),
    (f"{O}/identity", "IdentityAdminHub (domains, IdP, SCIM, JIT)", "enterprise_identity", "org:OWNER (nav)", "auth"),
    (f"{O}/audit", "AuditExplorer", "", "org:OWNER (nav)", "audit-log"),
    ("/admin", "SuperAdminGuard shell (no index route)", "", "superadmin", ""),
    ("/admin/margins", "AdminMarginsHub (Unit economics)", "", "superadmin", ""),
    ("/admin/sovereign", "SovereignConsole", "", "superadmin", ""),
    ("/admin/revops", "RevOpsConsole", "", "superadmin", ""),
    ("/partners", "PartnerPortal (+PartnerManifestConsole)", "", "partner member", ""),
    ("/", "LegacyRouteRedirect", "", "signed-in", ""),
    ("/work-items/*", "LegacyRouteRedirect", "", "signed-in", ""),
    ("/assistant/*", "LegacyRouteRedirect", "", "signed-in", ""),
    ("/automation/*", "LegacyRouteRedirect", "", "signed-in", ""),
    ("/notifications/*", "LegacyRouteRedirect", "", "signed-in", ""),
    ("/settings/*", "LegacyRouteRedirect", "", "signed-in", ""),
    ("/profile/*", "LegacyRouteRedirect", "", "signed-in", ""),
    ("/account/*", "LegacyRouteRedirect", "", "signed-in", ""),
    (f"{P}", "Dashboard (TenantGuard+DashboardLayout)", "", WS_ROLES, ""),
    (f"{P}/work-items", "WorkItems (documents list, upload)", "", WS_ROLES, "upload"),
    (f"{P}/work-items/:id", "WorkItemDetails (extraction view/correction)", "", WS_ROLES, "upload"),
    (f"{P}/assistant", "Assistant (RAG chat)", "", WS_ROLES, ""),
    (f"{P}/assistant/c/:conversationId", "AssistantCanvas", "", WS_ROLES, ""),
    (f"{P}/automation", "Automation (FlowBuilder, RuleTestDialog)", "", WS_ROLES, "workflows"),
    (f"{P}/automation/timeline", "ExecutionTimeline (run history)", "", WS_ROLES, "workflows"),
    (f"{P}/verification", "ReviewHub (+VerificationReviewQueue)", "", WS_ROLES, "review-queue"),
    (f"{P}/assertions", "AssertionReviewPage (+AssertionReviewQueue)", "semantic_assertions", WS_ROLES, "review-queue"),
    (f"{P}/redactions/:jobId", "RedactionStudio (deep link only)", "redaction", WS_ROLES, ""),
    (f"{P}/radar", "ForensicAuditRadar", "anomaly_radar", WS_ROLES, ""),
    (f"{P}/extraction-memory", "ExtractionMemory", "extraction_memory", WS_ROLES, ""),
    (f"{P}/entities", "Entities", "entity_graph", WS_ROLES, ""),
    (f"{P}/entities/:entityId", "Entity360", "entity_graph", WS_ROLES, ""),
    (f"{P}/cases", "Cases", "case_intelligence", WS_ROLES, ""),
    (f"{P}/cases/:caseId", "CaseDetail", "case_intelligence", WS_ROLES, ""),
    (f"{P}/packet-splits", "PacketSplits", "case_intelligence", WS_ROLES, ""),
    (f"{P}/packet-splits/:splitId", "SplitReview", "case_intelligence", WS_ROLES, ""),
    (f"{P}/tables", "Tables", "table_intelligence", WS_ROLES, ""),
    (f"{P}/tables/:tableId", "TableViewer", "table_intelligence", WS_ROLES, ""),
    (f"{P}/corroboration", "Corroborations", "universal_corroborator", WS_ROLES, ""),
    (f"{P}/corroboration/:runId", "CorroborationRun", "universal_corroborator", WS_ROLES, ""),
    (f"{P}/obligations", "Obligations", "obligations", WS_ROLES, ""),
    (f"{P}/obligations/:obligationId", "ObligationDetail", "obligations", WS_ROLES, ""),
    (f"{P}/erp", "ErpPosting", "erp_posting", WS_ROLES, ""),
    (f"{P}/erp/postings/:postingId", "ErpPostingDetail", "erp_posting", WS_ROLES, ""),
    (f"{P}/erp/targets/:targetId", "ErpTargetDetail", "erp_posting", WS_ROLES, ""),
    (f"{P}/process", "ProcessIntelligence", "process_intelligence", WS_ROLES, ""),
    (f"{P}/process/proposals/:proposalId", "ProcessIntelligence (proposal)", "process_intelligence", WS_ROLES, ""),
    (f"{P}/procurement", "ProcurementCaseQueue (three-way matching)", "reconciliation", WS_ROLES, ""),
    (f"{P}/procurement/policies", "TolerancePolicyEditor", "reconciliation", WS_ROLES, ""),
    (f"{P}/procurement/:caseId", "ThreeWayComparison", "reconciliation", WS_ROLES, ""),
    (f"{P}/notifications", "Notifications", "", WS_ROLES, ""),
    (f"{P}/settings", "Settings (tabs: profile, sessions [account]; workspace, ai, email, document [CONTRIBUTOR+])", "", "account tabs: any; workspace tabs: ws:CONTRIBUTOR+", "settings"),
    ("*", "NotFound", "", "anyone", ""),
]
page_findings = {"/admin": "F-009", f"{O}/analytics": "F-005", "/request/:token": "F-014",
                 f"{O}/audit": "F-008", f"{P}/settings": "F-008"}
for route, comp, cap, roles, crit in pages:
    add("page", route, comp, plan_for(cap), roles, findings=page_findings.get(route, ""), critical=crit)

# ------------------------------------------------------------------ nav items
navs = [
    ("ws:overview", "Workspace > Overview", "", WS_ROLES, ""),
    ("ws:notifications", "Workspace > Notifications", "", WS_ROLES, ""),
    ("ws:documents", "Document intelligence > Documents", "", WS_ROLES, "upload"),
    ("ws:assistant", "Document intelligence > AI Assistant", "", WS_ROLES, ""),
    ("ws:extraction-memory", "Document intelligence > Extraction memory (locked below Business)", "extraction_memory", WS_ROLES, ""),
    ("ws:entities", "Document intelligence > Entity graph (locked below Business)", "entity_graph", WS_ROLES, ""),
    ("ws:cases", "Document intelligence > Cases (locked below Business)", "case_intelligence", WS_ROLES, ""),
    ("ws:packet-splits", "Document intelligence > Scanned packets (locked below Business)", "case_intelligence", WS_ROLES, ""),
    ("ws:tables", "Document intelligence > Tables (locked below Business)", "table_intelligence", WS_ROLES, ""),
    ("ws:obligations", "Document intelligence > Obligations (locked below Business)", "obligations", WS_ROLES, ""),
    ("ws:procurement", "Enterprise processing > Three-way matching (locked below Business)", "reconciliation", WS_ROLES, ""),
    ("ws:erp", "Enterprise processing > ERP posting (locked below Business)", "erp_posting", WS_ROLES, ""),
    ("ws:process", "Enterprise processing > Process intelligence (Enterprise only)", "process_intelligence", WS_ROLES, ""),
    ("ws:radar", "Enterprise processing > Forensic audit radar (locked below Business)", "anomaly_radar", WS_ROLES, ""),
    ("ws:corroboration", "Enterprise processing > Document corroborator (Enterprise only)", "universal_corroborator", WS_ROLES, ""),
    ("ws:workflows", "Automation > Workflows", "", WS_ROLES, "workflows"),
    ("ws:run-history", "Automation > Run history", "", WS_ROLES, "workflows"),
    ("ws:review-queue", "Human review > Review queue", "", WS_ROLES, "review-queue"),
    ("ws:assertion-reviews", "Human review > Clause assertions (Enterprise only)", "semantic_assertions", WS_ROLES, "review-queue"),
    ("ws:settings", "Configuration > Settings (hidden for VIEWER via minimumRole)", "", "ws:CONTRIBUTOR+", "settings"),
    ("palette:procurement-policies", "Command palette shortcut > Tolerance policies", "reconciliation", WS_ROLES, ""),
    ("palette:create-workspace", "Command palette > Create workspace", "", "org member", ""),
    ("org:general", "Organization > General", "", "org:any", ""),
    ("org:notifications", "Organization > Notifications", "", "org:any", ""),
    ("org:members", "Organization > Members", "", "org:OWNER,ADMIN", "team"),
    ("org:transactional-email", "Organization > Transactional email (locked below Business)", "custom_email", "org:OWNER,ADMIN", ""),
    ("org:service-levels", "Organization > Service levels", "", "org:OWNER,ADMIN", ""),
    ("org:compliance", "Organization > Data governance & compliance", "", "org:OWNER,ADMIN", "data-governance"),
    ("org:developer", "Organization > Developer platform (locked below Developer)", "developer_api", "org:OWNER,ADMIN", "api-keys-webhooks"),
    ("org:byok", "Organization > Enterprise BYOK & models (no plan lock)", "", "org:OWNER,ADMIN", ""),
    ("org:branding", "Organization > Branding & custom domains (locked below Developer)", "custom_branding", "org:OWNER,ADMIN", ""),
    ("org:analytics", "Organization > Analytics & BI egress (NO UI lock; server needs addon.warehouse_sync)", "warehouse_sync", "org:OWNER,ADMIN", ""),
    ("org:marketplace", "Organization > Partner marketplace (no plan lock)", "", "org:OWNER,ADMIN", ""),
    ("org:autonomy", "Organization > Calibrated autonomy (Enterprise only)", "calibrated_autonomy", "org:OWNER,ADMIN", ""),
    ("org:egress", "Organization > Egress lockdown (Enterprise only)", "egress_lockdown", "org:OWNER,ADMIN", ""),
    ("org:billing", "Organization > Billing", "", "org:OWNER,BILLING", "billing"),
    ("org:api-keys", "Organization > API keys (locked below Developer)", "developer_api", "org:OWNER", "api-keys-webhooks"),
    ("org:webhooks", "Organization > Webhooks (locked below Developer)", "outgoing_webhooks", "org:OWNER", "api-keys-webhooks"),
    ("org:identity", "Organization > Enterprise identity (Enterprise only)", "enterprise_identity", "org:OWNER", "auth"),
    ("org:audit", "Organization > Audit log", "", "org:OWNER", "audit-log"),
    ("platform:margins", "Platform > Unit economics", "", "superadmin", ""),
    ("platform:sovereign", "Platform > Sovereign edition", "", "superadmin", ""),
    ("platform:revops", "Platform > Revenue operations", "", "superadmin", ""),
    ("partner:portal", "Partner > Partner portal (shown only to partner members)", "", "partner member", ""),
    ("global:search", "Global search / command palette (Ctrl+K)", "", "signed-in", ""),
    ("global:workspace-switcher", "Org/workspace switcher (OrgWorkspaceSwitcher)", "", "signed-in", ""),
    ("global:sign-out", "Sign out", "", "signed-in", "auth"),
    ("global:theme-toggle", "Theme toggle", "", "anyone", ""),
    ("global:mobile-drawer", "Mobile sidebar drawer", "", "signed-in", ""),
]
nav_findings = {"org:analytics": "F-005", "org:developer": "F-004", "org:identity": "F-004",
                "org:byok": "F-005", "org:marketplace": "F-005"}
for id_, desc, cap, roles, crit in navs:
    add("nav_item", id_, desc, plan_for(cap), roles, findings=nav_findings.get(id_, ""), critical=crit)

# ------------------------------------------------------------------ ui actions (seed list; Phase 3 expands per page)
ui = [
    ("auth.login", "Sign in with email/password", "auth"), ("auth.register", "Create account", "auth"),
    ("auth.verify-email", "Verify email link + resend", "auth"), ("auth.forgot-reset", "Forgot + reset password", "auth"),
    ("auth.sso-login", "SSO discover/start/complete", "auth"), ("auth.session-expiry", "Access-token expiry -> refresh -> re-login", "auth"),
    ("auth.change-password", "Change password (Settings > profile)", "auth"), ("auth.sessions-revoke", "Revoke a session / log out all", "auth"),
    ("auth.email-change", "Request + confirm email change", "auth"), ("auth.step-up", "Step-up re-auth modal on sensitive actions", "auth"),
    ("org.create", "Create organization (onboarding)", ""), ("org.archive", "Archive organization (OWNER)", ""),
    ("ws.create", "Create workspace", ""), ("team.invite", "Invite member", "team"), ("team.accept", "Accept invitation", "team"),
    ("team.role-change", "Change member role", "team"), ("team.remove", "Remove member", "team"),
    ("team.ownership-transfer", "Transfer ownership (propose/accept/decline/cancel)", "team"),
    ("docs.upload-single", "Upload one document", "upload"), ("docs.upload-batch", "Batch upload / archive expand", "upload"),
    ("docs.bulk-actions", "Bulk actions on work items", "upload"), ("docs.correct-field", "Correct an extracted field", "upload"),
    ("review.approve", "Approve review item", "review-queue"), ("review.reject", "Reject review item", "review-queue"),
    ("review.escalate", "Escalate / assign review item", "review-queue"), ("review.bulk", "Bulk review actions", "review-queue"),
    ("assertions.resolve", "Resolve clause assertion review", "review-queue"),
    ("wf.create", "Create workflow in builder", "workflows"), ("wf.validate", "Validate / test rule", "workflows"),
    ("wf.run", "Workflow runs on document event", "workflows"), ("wf.failure", "Workflow failure handling + run history", "workflows"),
    ("assistant.ask", "Ask assistant (RAG) incl. unanswerable question", ""), ("assistant.stream", "Streamed answer + resume", ""),
    ("billing.checkout", "Test-mode checkout", "billing"), ("billing.portal", "Open billing portal", "billing"),
    ("billing.seats", "Change seats", "billing"), ("billing.cancel", "Cancel / downgrade", "billing"),
    ("billing.quota-reached", "Quota reached behaviour", "billing"), ("billing.addon", "Buy add-on", "billing"),
    ("keys.create", "Create API key (shown once)", "api-keys-webhooks"), ("keys.revoke", "Revoke API key", "api-keys-webhooks"),
    ("webhooks.create", "Create webhook endpoint", "api-keys-webhooks"), ("webhooks.test", "Send test / redeliver", "api-keys-webhooks"),
    ("gov.export", "Data export (compliance)", "data-governance"), ("gov.erasure", "Erasure / delete subject", "data-governance"),
    ("gov.retention", "Retention policy + legal hold", "data-governance"), ("audit.browse-export", "Browse / filter / export audit log", "audit-log"),
    ("settings.ai", "Workspace AI settings", "settings"), ("settings.document", "Document settings", "settings"),
    ("settings.email", "Workspace email settings", "settings"), ("settings.unsaved-guard", "Unsaved-changes dialog blocks navigation", "settings"),
    ("locked.upgrade-dialog", "Locked nav item opens upgrade dialog", ""), ("redaction.apply", "Redaction studio: detect, edit, apply, bundle", ""),
    ("byok.configure", "BYOK add/rotate credential", ""), ("branding.domain", "Claim/verify custom domain", ""),
]
for id_, desc, crit in ui:
    add("ui_action", id_, desc + " (seed row; Phase 3 enumerates every button per page)", critical=crit)

# ------------------------------------------------------------------ endpoints
tests_dir = BACKEND / "tests"
test_files = [str(p.relative_to(BACKEND)) for p in tests_dir.rglob("test_*.py")]
CRIT_MOD = {
    "auth.py": "auth", "email_change.py": "auth", "me.py": "auth", "saml.py": "auth", "scim.py": "auth",
    "identity_admin.py": "auth", "avatar.py": "",
    "upload.py": "upload", "ingestion.py": "upload", "work_items.py": "upload", "public_document_requests.py": "upload",
    "verifications.py": "review-queue", "review.py": "review-queue", "review_collab.py": "review-queue",
    "assertions.py": "review-queue",
    "automation.py": "workflows",
    "billing.py": "billing", "billing_webhook.py": "billing", "billing_webhook_multi.py": "billing",
    "entitlements.py": "billing", "usage.py": "billing",
    "organizations.py": "team", "workspaces.py": "team", "organization_invitations.py": "team",
    "ownership_transfers.py": "team",
    "api_keys.py": "api-keys-webhooks", "webhooks.py": "api-keys-webhooks", "developer.py": "api-keys-webhooks",
    "public/gateway.py": "api-keys-webhooks",
    "compliance.py": "data-governance", "audit_logs.py": "audit-log",
}
TEST_HINT = {
    "auth.py": ["services/test_auth_endpoints.py", "api/test_login_backoff.py"],
    "api_keys.py": ["api/test_api_key_issuance.py"], "audit_logs.py": ["api/test_audit_logs.py", "api/test_audit_export.py"],
    "avatar.py": ["api/test_avatar_upload.py"], "byok.py": ["api/test_byok_endpoints.py"],
    "admin/cogs.py": ["api/test_cogs_endpoints.py"], "compliance.py": ["api/test_compliance_endpoints.py"],
    "email_change.py": ["api/test_email_change.py"], "organization_email_settings.py": ["api/test_organization_email_settings.py"],
    "organization_invitations.py": ["api/test_organization_invitations.py"], "ownership_transfers.py": ["api/test_ownership_transfers.py"],
    "me.py": ["api/test_profile.py", "api/test_me_context_logo.py"], "public/gateway.py": ["api/test_public_api_endpoints.py"],
    "slos.py": ["api/test_slo_endpoints.py"], "upload.py": ["api/test_upload_authorization.py", "api/test_logo_route.py"],
    "usage.py": ["api/test_arch14_gate_14_7_usage_api.py"], "verifications.py": ["api/test_arch13_gate_13_8_verification_api.py"],
    "scim.py": ["api/test_arch16_scim_dunning.py"], "saml.py": ["security/test_saml_xsw_rig.py"],
    "assistant_stream.py": ["api/test_arch12_stream_api.py"],
}
ep_findings = {
    "developer.py": "F-004", "identity_admin.py": "", "review_collab.py": "",
    "billing_webhook.py": "F-013", "billing_webhook_multi.py": "F-013",
}
ungated_flag = {("PUT", "/api/v1/organizations/{organization_id}/identity/security-policy"),
                ("POST", "/api/v1/organizations/{organization_id}/identity/domains/{domain_id}/verify"),
                ("GET", "/api/v1/workspaces/{workspace_id}/review/collab/state"),
                ("WEBSOCKET", "/api/v1/workspaces/{workspace_id}/review/collab/live")}
NO_API_TESTS = {"procurement.py", "redactions.py", "assertions.py", "anomalies.py", "autonomy.py",
                "extraction_memory.py", "entities.py", "packet_splits.py", "cases.py", "tables.py",
                "corroboration.py", "obligations.py", "erp.py", "review.py", "review_collab.py",
                "process_intel.py", "egress.py", "admin/sovereign.py", "admin/revops.py",
                "billing_webhook.py", "billing_webhook_multi.py", "webhooks.py", "public_calendar_feeds.py",
                "public_document_requests.py"}
def normalize_roles(text):
    out = []
    for part in text.split(";"):
        m = re.fullmatch(r"ws_role:(\w+)", part) or re.fullmatch(r"ws_(viewer|contributor|admin)", part)
        if m:
            out.append(f"ws:{m.group(1).upper()}+")
        elif part == "org_owner":
            out.append("org:OWNER")
        elif part == "org_admin":
            out.append("org:OWNER,ADMIN")
        elif part == "org_member":
            out.append("org:any")
        elif part.startswith("org_roles:"):
            out.append("org:" + part.split(":", 1)[1])
        else:
            out.append(part)
    return ";".join(dict.fromkeys(out))


for r in csv.DictReader(open(SP / "endpoints.csv")):
    mod = r["module"]
    caps = [c for c in r["capability"].split(";") if c]
    if r["plan_gated"] == "yes":
        if not caps:
            caps = {"custom_domains.py": ["custom_domain"], "warehouse_sync.py": ["warehouse_sync"]}.get(mod, [])
        plan = "|".join(sorted({plan_for(c) for c in caps if c != "ADDON_CATALOG"})) or "any (add-on purchase)"
        if mod == "review.py":
            plan = "any (per-kind: items filtered by each kind's capability)"
    else:
        gated_module_caps = {
            "api_keys.py": "developer_api", "webhooks.py": "outgoing_webhooks",
            "identity_admin.py": "enterprise_identity", "warehouse_sync.py": "warehouse_sync",
            "custom_domains.py": "custom_domain", "tenant_branding.py": "custom_branding",
            "email_settings.py": "custom_email", "organization_email_settings.py": "custom_email",
            "developer.py": "developer_api", "review_collab.py": "collaborative_review",
            "extraction_memory.py": "extraction_memory", "entities.py": "entity_graph",
        }
        plan = "any"
        if mod in gated_module_caps:
            plan = f"any (UNGATED in a {plan_for(gated_module_caps[mod])}-plan module)"
    auth = r["auth"]
    roles = r["roles"] or {"public": "anonymous", "public_token": "token holder", "signature": "gateway signature",
                           "scim_token": "SCIM bearer token", "api_key": "API key (scopes)"}.get(auth, "any authenticated user")
    roles = normalize_roles(roles)
    desc = f"{r['handler']} [{mod}:{r['line']}] auth={auth} scope={r['scope']}"
    if mod in NO_API_TESTS:
        desc += " (no existing API test)"
    f = []
    if mod in NO_API_TESTS:
        f.append("F-002")
    if ep_findings.get(mod):
        f.append(ep_findings[mod])
    if (r["method"], r["path"]) in ungated_flag:
        f.append("F-004")
    if mod in ("api_keys.py", "audit_logs.py", "webhooks.py"):
        f.append("F-015")
    if r["method"] == "WEBSOCKET":
        f.append("F-012")
    if mod == "public_document_requests.py":
        f.append("F-014")
    if mod in ("warehouse_sync.py",) and r["plan_gated"] == "no":
        pass
    test = ";".join("existing:tests/" + t for t in TEST_HINT.get(mod, []))
    add("endpoint", f"{r['method']} {r['path']}", desc, plan, roles, test, ";".join(dict.fromkeys(f)),
        critical=CRIT_MOD.get(mod, ""))

# reranker service (separate app)
for m, p in (("GET", "/health"), ("GET", "/ready"), ("POST", "/rerank")):
    add("endpoint", f"{m} reranker:8081{p}", "app/reranker/main.py (separate internal service; /rerank needs RERANKER_INTERNAL_TOKEN)",
        "n/a", "internal token" if p == "/rerank" else "anonymous")

# ------------------------------------------------------------------ background jobs
handlers_init = (BACKEND / "app/workers/handlers/__init__.py").read_text()
job_types = sorted(set(re.findall(r'"([a-z_]+\.[a-z_]+)"', handlers_init.split("ALL_PHASE_JOB_TYPES")[0])))
sched = dict()
for m in re.finditer(r'ScheduledJob\(\s*job_type="([^"]+)",\s*interval_seconds=([0-9_]+)(?:,\s*at_hour=(\d+))?',
                     (BACKEND / "app/workers/scheduler.py").read_text()):
    sched[m.group(1)] = f"every {int(m.group(2).replace('_', ''))}s" + (f" at {m.group(3)}:00" if m.group(3) else "")
EXTERNAL = {"process.sweep_workspace": "enqueued by scripts/sweep_process.py (host cron)",
            "revops.sweep": "enqueued by scripts/sweep_revops.py (host cron)"}
CRIT_JOB = {"document": "upload", "batch": "upload", "work_items": "upload", "ingestion": "upload",
            "pipeline": "upload", "automation": "workflows", "billing": "billing", "usage": "billing",
            "notification": "", "identity": "auth"}
job_findings = {"billing.reconcile": "F-006", "billing.assemble_invoice": "F-006", "usage.reconcile": "F-006",
                "billing.seat_sync": "F-006"}
for jt in job_types:
    src = sched.get(jt) or EXTERNAL.get(jt) or "enqueued on demand by services"
    add("background_job", jt, f"worker job type; trigger: {src}", findings=job_findings.get(jt, ""),
        critical=CRIT_JOB.get(jt.split(".")[0], ""))
for loop in ("relay", "delivery", "jobs(light)", "jobs(ocr)", "jobs(enrich)", "stripe", "scheduler"):
    add("background_job", f"worker-loop:{loop}", f"python -m app.worker --loop {loop.split('(')[0]} (docker-compose service)",
        critical="billing" if loop == "stripe" else "")
cron = (BACKEND / "deploy/cron.d/flowpilot-sweepers").read_text() + (BACKEND / "deploy/cron.d/flowpilot-backups").read_text()
for line in cron.splitlines():
    m = re.match(r"([0-9*/,-]+ [0-9*/,-]+ [0-9*]+ [0-9*]+ [0-9*]+)\s+\S+\s+\S+/(flowpilot-sweep(?:-watchdog)?)\s*(\S*)\s*(.*)", line)
    if m:
        name = m.group(3) or "watchdog"
        crit = "data-governance" if name in ("compliance", "backup", "restore-drill", "base-backup", "pitr-drill") else ""
        add("background_job", f"cron:{name}", f"host cron '{m.group(1)}' {m.group(2)} {m.group(3)} {m.group(4)} (not run by docker-compose.prod.yml)",
            findings="F-006", critical=crit)

# ------------------------------------------------------------------ migrations
revs, downs = {}, {}
for f in sorted((BACKEND / "alembic/versions").glob("*.py")):
    t = ast.parse(f.read_text(encoding="utf-8-sig"))
    r = d = None
    for n in t.body:
        if isinstance(n, (ast.Assign, ast.AnnAssign)):
            tg = n.targets[0] if isinstance(n, ast.Assign) else n.target
            if isinstance(tg, ast.Name) and tg.id in ("revision", "down_revision"):
                v = ast.literal_eval(n.value)
                if tg.id == "revision":
                    r = v
                else:
                    d = v
    revs[r] = f.name
    downs[r] = d
head = [r for r in revs if r not in {d for d in downs.values()}]
order, cur = [], head[0]
while cur:
    order.append(cur)
    cur = downs[cur]
for i, r in enumerate(reversed(order), 1):
    add("migration", r, f"#{i:03d} {revs[r]} (down_revision={downs[r]})" + (" HEAD" if r == head[0] else ""))

# ------------------------------------------------------------------ config vars
insecure = {"API_KEY_PEPPER": "F-003", "REDIS_IDENTITY_PEPPER": "F-003", "EMAIL_ENCRYPTION_KEYS": "F-003",
            "POSTGRES_PASSWORD": "F-011", "S3_DEV_FALLBACK_CREDENTIALS": "F-011", "CORS_ORIGINS": "F-011",
            "SMTP_ALLOW_PRIVATE_IN_DEVELOPMENT": "F-011", "ENVIRONMENT": "F-011"}
crit_cfg = re.compile(r"JWT|TOKEN_EXPIRE|PASSWORD_RESET|STRIPE|DODO|BILLING|API_KEY|PEPPER|ENCRYPTION|CORS|ENVIRONMENT")
for line in open(SP / "config_vars.tsv"):
    cls, name, typ, default, ex, prod = line.rstrip("\n").split("\t")
    if "SecretStr(" in default or re.search(r"PEPPER|SECRET|KEYS?$|PASSWORD|TOKEN", name):
        default = re.sub(r"(['\"])[^'\"]{6,}\1", "'<redacted>'", default)
    desc = f"backend Settings.{name}: {typ} default={default}; in .env.example={ex}; in .env.production.template={prod}"
    crit = ""
    if re.search(r"JWT|TOKEN_EXPIRE|PASSWORD_RESET", name):
        crit = "auth"
    elif re.search(r"STRIPE|DODO|BILLING", name):
        crit = "billing"
    add("config_var", name, desc, findings=insecure.get(name, "F-011" if prod == "N" else ""), critical=crit)
for name, desc in (("VITE_API_URL", "frontend API base URL (read by services/api/client.ts; NOT in frontend/.env.example)"),
                   ("VITE_APP_NAME", "frontend/.env.example only; not read by code"),
                   ("VITE_APP_VERSION", "frontend/.env.example only; not read by code"),
                   ("VITE_ENVIRONMENT", "frontend/.env.example only; not read by code")):
    add("config_var", name, desc, findings="F-010")

# ------------------------------------------------------------------ integrations
integrations = [
    ("postgresql-pgvector", "PostgreSQL 16 + pgvector (primary + optional read replica)", ""),
    ("redis", "Redis 7 (rate limits, locks, replay guards, stream sessions)", ""),
    ("minio-s3", "MinIO / S3-compatible object storage (documents, logos, exports; regional buckets)", "upload"),
    ("stripe", "Stripe billing (checkout, portal, seats, webhooks, inbound worker loop)", "billing"),
    ("dodo-payments", "Dodo Payments billing gateway (multi-gateway webhook)", "billing"),
    ("groq", "Groq LLM (default provider)", ""), ("gemini", "Google Gemini LLM", ""),
    ("local-llm", "Local/OpenAI-compatible LLM endpoint", ""),
    ("byok-providers", "BYOK providers: OPENAI, ANTHROPIC, GROQ, AZURE_OPENAI, MISTRAL, GEMINI", ""),
    ("paddleocr", "PaddleOCR (OCR worker profile)", "upload"),
    ("sentence-transformers", "SentenceTransformers embeddings (enrich profile)", "upload"),
    ("reranker", "Internal reranker service app/reranker (port 8081)", ""),
    ("platform-smtp", "Platform SMTP (verification, reset, invitations)", "auth"),
    ("tenant-smtp", "Tenant custom SMTP (transactional email override)", ""),
    ("saml-idp", "SAML 2.0 SSO (ACS, SLO, metadata)", "auth"), ("oidc-idp", "OIDC SSO callback", "auth"),
    ("scim", "SCIM 2.0 directory sync (/scim/v2)", "auth"),
    ("dns", "DNS TXT verification (identity domains, custom domains)", ""),
    ("caddy-acme", "Caddy on-demand TLS 'ask' endpoint + ACME", ""),
    ("outgoing-webhooks", "Tenant outgoing webhooks (signed, retried; SSRF client)", "api-keys-webhooks"),
    ("public-api-gateway", "Public developer API (/api/v1/public, API keys)", "api-keys-webhooks"),
    ("erp-http", "ERP HTTP targets: QuickBooks Online, Zoho Books, Business Central, S/4HANA OData, NetSuite REST, generic REST/OData", ""),
    ("erp-sftp", "ERP SFTP transport (X12 997 / ack files; host key pinned)", ""),
    ("erp-formats", "ERP file formats: CSV, XLSX, X12, UBL 2.1 (XSD), Tally, JSON", ""),
    ("erp-mocks", "scripts/erp_mock_targets.py mock ERP targets for tests", ""),
    ("warehouse-bigquery", "Analytics egress: BigQuery", ""), ("warehouse-snowflake", "Analytics egress: Snowflake", ""),
    ("warehouse-databricks", "Analytics egress: Databricks", ""), ("warehouse-s3-bundle", "Analytics egress: S3 bundle", ""),
    ("ical-feeds", "Public iCalendar feeds for obligations (token URL)", ""),
    ("marketplace-signing", "Partner marketplace manifest signing/verification", ""),
    ("backups-dr", "Backup, PITR and restore drills (scripts/dr_*.py, restore_drill.py via host cron)", "data-governance"),
]
for id_, desc, crit in integrations:
    add("integration", id_, desc, findings="F-006" if id_ == "backups-dr" else "", critical=crit)

OUT.parent.mkdir(parents=True, exist_ok=True)
with open(OUT, "w", newline="") as fh:
    w = csv.DictWriter(fh, fieldnames=COLS, lineterminator="\n")
    w.writeheader()
    w.writerows(rows)
from collections import Counter
print(Counter(r["type"] for r in rows), len(rows))
print("critical rows:", sum(1 for r in rows if r["description"].startswith("[CRITICAL")))

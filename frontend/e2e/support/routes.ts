/**
 * Every page the router defines (frontend/src/App.tsx), grouped the way the
 * sidebar groups them. `id` matches the COVERAGE.csv `page` row id.
 */
export interface WorkspacePage {
  readonly id: string;
  readonly sub: string;
  readonly title: RegExp;
  readonly plan: "any" | "developer" | "business" | "enterprise";
  readonly navId?: string;
}

export const WORKSPACE_PAGES: readonly WorkspacePage[] = [
  { id: "/:orgSlug/:workspaceSlug", sub: "", title: /Recent Activity/, plan: "any", navId: "ws:overview" },
  { id: "/:orgSlug/:workspaceSlug/notifications", sub: "notifications", title: /Notifications/, plan: "any", navId: "ws:notifications" },
  { id: "/:orgSlug/:workspaceSlug/work-items", sub: "work-items", title: /Documents Database/, plan: "any", navId: "ws:documents" },
  { id: "/:orgSlug/:workspaceSlug/assistant", sub: "assistant", title: /AI Assistant/, plan: "any", navId: "ws:assistant" },
  { id: "/:orgSlug/:workspaceSlug/extraction-memory", sub: "extraction-memory", title: /Extraction memory/, plan: "business", navId: "ws:extraction-memory" },
  { id: "/:orgSlug/:workspaceSlug/entities", sub: "entities", title: /Entity graph/, plan: "business", navId: "ws:entities" },
  { id: "/:orgSlug/:workspaceSlug/cases", sub: "cases", title: /Cases/, plan: "business", navId: "ws:cases" },
  { id: "/:orgSlug/:workspaceSlug/packet-splits", sub: "packet-splits", title: /Scanned packets/, plan: "business", navId: "ws:packet-splits" },
  { id: "/:orgSlug/:workspaceSlug/tables", sub: "tables", title: /Tables/, plan: "business", navId: "ws:tables" },
  { id: "/:orgSlug/:workspaceSlug/obligations", sub: "obligations", title: /Obligations/, plan: "business", navId: "ws:obligations" },
  { id: "/:orgSlug/:workspaceSlug/batches", sub: "batches", title: /Batch operations/, plan: "business", navId: "ws:batches" },
  { id: "/:orgSlug/:workspaceSlug/procurement", sub: "procurement", title: /Invoice matching/, plan: "business", navId: "ws:procurement" },
  { id: "/:orgSlug/:workspaceSlug/procurement/policies", sub: "procurement/policies", title: /Matching tolerances/, plan: "business", navId: "palette:procurement-policies" },
  { id: "/:orgSlug/:workspaceSlug/erp", sub: "erp", title: /ERP posting/, plan: "business", navId: "ws:erp" },
  { id: "/:orgSlug/:workspaceSlug/process", sub: "process", title: /Process intelligence/, plan: "enterprise", navId: "ws:process" },
  { id: "/:orgSlug/:workspaceSlug/radar", sub: "radar", title: /Audit radar/, plan: "business", navId: "ws:radar" },
  { id: "/:orgSlug/:workspaceSlug/corroboration", sub: "corroboration", title: /Document corroborator/, plan: "enterprise", navId: "ws:corroboration" },
  { id: "/:orgSlug/:workspaceSlug/truthmesh", sub: "truthmesh", title: /TruthMesh/, plan: "business", navId: "ws:truthmesh" },
  { id: "/:orgSlug/:workspaceSlug/automation", sub: "automation", title: /Automation Dashboard/, plan: "any", navId: "ws:workflows" },
  { id: "/:orgSlug/:workspaceSlug/automation/timeline", sub: "automation/timeline", title: /Execution traces/, plan: "any", navId: "ws:run-history" },
  { id: "/:orgSlug/:workspaceSlug/verification", sub: "verification", title: /Review/, plan: "any", navId: "ws:review-queue" },
  { id: "/:orgSlug/:workspaceSlug/assertions", sub: "assertions", title: /Clause assertions/, plan: "enterprise", navId: "ws:assertion-reviews" },
  { id: "/:orgSlug/:workspaceSlug/settings", sub: "settings", title: /Settings/, plan: "any", navId: "ws:settings" },
];

export interface OrganizationPage {
  readonly id: string;
  readonly sub: string;
  readonly title: RegExp;
  readonly plan: "any" | "developer" | "business" | "enterprise";
  readonly navId: string;
}

export const ORGANIZATION_PAGES: readonly OrganizationPage[] = [
  { id: "/organizations/:orgSlug/settings", sub: "settings", title: /Organization profile/, plan: "any", navId: "org:general" },
  { id: "/organizations/:orgSlug/notifications", sub: "notifications", title: /Organization notifications/, plan: "any", navId: "org:notifications" },
  { id: "/organizations/:orgSlug/members", sub: "members", title: /Members/, plan: "any", navId: "org:members" },
  { id: "/organizations/:orgSlug/email-settings", sub: "email-settings", title: /Transactional Email/, plan: "business", navId: "org:transactional-email" },
  // N-002: every plan reads its service levels; setting its own targets is Enterprise (locked below).
  { id: "/organizations/:orgSlug/service-levels", sub: "service-levels", title: /Service levels/, plan: "enterprise", navId: "org:service-levels" },
  { id: "/organizations/:orgSlug/compliance", sub: "compliance", title: /Data governance/, plan: "any", navId: "org:compliance" },
  { id: "/organizations/:orgSlug/developer", sub: "developer", title: /Developer platform/, plan: "developer", navId: "org:developer" },
  { id: "/organizations/:orgSlug/byok", sub: "byok", title: /BYOK/, plan: "business", navId: "org:byok" },
  { id: "/organizations/:orgSlug/branding", sub: "branding", title: /Branding & custom domains/, plan: "developer", navId: "org:branding" },
  { id: "/organizations/:orgSlug/analytics", sub: "analytics", title: /Analytics & BI egress/, plan: "business", navId: "org:analytics" },
  { id: "/organizations/:orgSlug/marketplace", sub: "marketplace", title: /Partner marketplace/, plan: "any", navId: "org:marketplace" },
  { id: "/organizations/:orgSlug/autonomy", sub: "autonomy", title: /Calibrated autonomy/, plan: "enterprise", navId: "org:autonomy" },
  { id: "/organizations/:orgSlug/egress", sub: "egress", title: /Egress lockdown/, plan: "enterprise", navId: "org:egress" },
  { id: "/organizations/:orgSlug/billing", sub: "billing", title: /Billing/, plan: "any", navId: "org:billing" },
  { id: "/organizations/:orgSlug/api-keys", sub: "api-keys", title: /API keys/, plan: "developer", navId: "org:api-keys" },
  { id: "/organizations/:orgSlug/webhooks", sub: "webhooks", title: /Webhooks/, plan: "developer", navId: "org:webhooks" },
  { id: "/organizations/:orgSlug/identity", sub: "identity", title: /Enterprise identity/, plan: "enterprise", navId: "org:identity" },
  { id: "/organizations/:orgSlug/audit", sub: "audit", title: /Audit log/, plan: "any", navId: "org:audit" },
];

/** Text a lower plan's lock card or add-on card shows instead of the feature. */
export const LOCK_TEXT = /included on the|included on higher plans|isn.t included|not included in your plan|is an add-on|upgrade/i;

/** Nav label in the sidebar, by nav id (navigation.ts / OrganizationSidebarNavigation.tsx). */
export const NAV_LABEL: Record<string, string> = {
  "ws:extraction-memory": "Extraction memory",
  "ws:entities": "Entity graph",
  "ws:cases": "Cases",
  "ws:packet-splits": "Scanned packets",
  "ws:tables": "Tables",
  "ws:obligations": "Obligations",
  "ws:procurement": "Three-way matching",
  "ws:erp": "ERP posting",
  "ws:process": "Process intelligence",
  "ws:radar": "Forensic audit radar",
  "ws:corroboration": "Document corroborator",
  "ws:truthmesh": "TruthMesh",
  "ws:assertion-reviews": "Clause assertions",
};

export const PLATFORM_PAGES = [
  { id: "/admin/margins", path: "/admin/margins", title: /Unit economics/, navId: "platform:margins" },
  { id: "/admin/sovereign", path: "/admin/sovereign", title: /Sovereign edition/, navId: "platform:sovereign" },
  { id: "/admin/revops", path: "/admin/revops", title: /Revenue operations/, navId: "platform:revops" },
] as const;

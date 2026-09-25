import type { LucideIcon } from "lucide-react";
import {
  BarChart3,
  Brain,
  Network,
  FolderKanban,
  Scissors,
  Table2,
  Bell,
  ClipboardCheck,
  CreditCard,
  FileText,
  GitCompareArrows,
  GitCompare,
  Handshake,
  History,
  KeyRound,
  KeySquare,
  LayoutDashboard,
  ListChecks,
  Mail,
  MessageSquare,
  PlusSquare,
  Radar,
  ScrollText,
  Gauge,
  Palette,
  TerminalSquare,
  Shield,
  Settings,
  ShieldCheck,
  SlidersHorizontal,
  Store,
  Sliders,
  Target,
  Scale,
  Users,
  Webhook,
} from "lucide-react";

import { CAPABILITY, type CapabilityKey } from "@/constants/capabilities";
import {
  ROUTE_PATTERNS,
  assertionsPath,
  assistantPath,
  automationPath,
  automationTimelinePath,
  extractionMemoryPath,
  entitiesPath,
  casesPath,
  packetSplitsPath,
  tablesPath,
  corroborationsPath,
  createWorkspacePath,
  notificationsPath,
  organizationApiKeysPath,
  organizationAuditPath,
  organizationBillingPath,
  organizationAnalyticsPath,
  organizationBrandingPath,
  organizationMarketplacePath,
  organizationAutonomyPath,
  organizationBYOKPath,
  organizationCompliancePath,
  organizationDeveloperPath,
  organizationEmailPath,
  organizationIdentityPath,
  organizationMembersPath,
  organizationNotificationsPath,
  organizationSettingsPath,
  organizationSLOsPath,
  organizationWebhooksPath,
  partnerPortalPath,
  platformMarginsPath,
  procurementPath,
  procurementPoliciesPath,
  radarPath,
  verificationPath,
  workItemsPath,
  workspaceDashboardPath,
  workspaceSettingsPath,
} from "@/routes/tenantPaths";
import type { WorkspaceRole } from "@/types/tenancy";

export interface NavigationItem {
  readonly name: string;
  readonly path: string;
  readonly icon: LucideIcon;
  /**
   * HM-S1:org-nav-capability. The capability whose writes the page needs.
   * Absent from the plan, the row shows a lock and opens the upgrade dialog.
   */
  readonly capability?: CapabilityKey;
}

/**
 * ARCH36-S1:grouped-navigation — the workspace navigation model.
 *
 * WHY EVERY ENTRY NAMES ITS ROUTE PATTERN
 * =======================================
 *
 * Five shipped pages (the audit radar, the matching queue, its tolerance
 * policies, the execution history and the partner portal) had a route and no
 * link. Each one was a page somebody built, registered in App.tsx and then
 * never connected, and nothing noticed because nothing could: a link is a
 * string, and a missing string is not a type error.
 *
 * `route` is typed as a key of ROUTE_PATTERNS, so a renamed pattern is a
 * compile error here. verify_arch36.py reads these keys back and fails when a
 * workspace pattern without a parameter has neither an entry below nor a
 * written reason for having none.
 *
 * WHY LOCKED ENTRIES ARE SHOWN, NOT HIDDEN
 * ========================================
 *
 * An entry with a `capability` renders with a lock when the organization's
 * tier does not grant it, and still links to its page, which renders the
 * existing lock card. Hiding it would make the upgrade invisible to the one
 * reader it is aimed at. The lock is not a security control: every endpoint
 * behind these pages refuses on the server through `capability_gate`.
 *
 * WHY THE GROUPS ARE DATA
 * =======================
 *
 * The sidebar and the command palette both render from this function. A
 * second list in the palette would drift from the first within a release.
 */
export type RoutePatternKey = keyof typeof ROUTE_PATTERNS;

export type WorkspaceNavigationGroupKey =
  | "workspace"
  | "intelligence"
  | "processing"
  | "automation"
  | "review"
  | "configuration";

export interface WorkspaceNavigationItem extends NavigationItem {
  /** Stable identifier. Tests and the command palette key on it. */
  readonly id: string;
  readonly route: RoutePatternKey;
  /** One line, shown in the command palette. */
  readonly description: string;
  readonly capability?: CapabilityKey;
  /** Omitted means every workspace role. */
  readonly minimumRole?: WorkspaceRole;
  /**
   * Match the path exactly. Needed where another entry's path extends this
   * one: the dashboard is the workspace root, and the execution history
   * lives under the workflows path.
   */
  readonly end?: boolean;
  readonly keywords?: readonly string[];
}

export interface WorkspaceNavigationGroup {
  readonly key: WorkspaceNavigationGroupKey;
  readonly label: string;
  readonly items: readonly WorkspaceNavigationItem[];
}

export const buildWorkspaceNavigationGroups = (
  orgSlug: string,
  workspaceSlug: string,
): readonly WorkspaceNavigationGroup[] => [
  {
    key: "workspace",
    label: "Workspace",
    items: [
      {
        id: "overview",
        name: "Overview",
        route: "workspaceDashboard",
        path: workspaceDashboardPath(orgSlug, workspaceSlug),
        icon: LayoutDashboard,
        description: "Workspace dashboard and processing health",
        end: true,
        keywords: ["home", "dashboard"],
      },
      {
        id: "notifications",
        name: "Notifications",
        route: "workspaceNotifications",
        path: notificationsPath(orgSlug, workspaceSlug),
        icon: Bell,
        description: "Every alert raised in this workspace",
        keywords: ["alerts", "inbox"],
      },
    ],
  },
  {
    key: "intelligence",
    label: "Document intelligence",
    items: [
      {
        id: "documents",
        name: "Documents",
        route: "workspaceWorkItems",
        path: workItemsPath(orgSlug, workspaceSlug),
        icon: FileText,
        description: "Upload, search and inspect processed documents",
        keywords: ["work items", "upload", "files", "ocr"],
      },
      {
        id: "assistant",
        name: "AI Assistant",
        route: "workspaceAssistant",
        path: assistantPath(orgSlug, workspaceSlug),
        icon: MessageSquare,
        description: "Ask questions across the workspace's documents",
        keywords: ["chat", "rag", "ask"],
      },
      // ARCH41-S3:nav-extraction-memory
      {
        id: "extraction-memory",
        name: "Extraction memory",
        route: "workspaceExtractionMemory",
        path: extractionMemoryPath(orgSlug, workspaceSlug),
        icon: Brain,
        description: "Layouts learned from reviewed corrections, proven by trial",
        capability: CAPABILITY.extractionMemory,
        keywords: ["learning", "corrections", "templates", "accuracy", "memory"],
      },
      // ARCH42-S2:nav-entities
      {
        id: "entities",
        name: "Entity graph",
        route: "workspaceEntities",
        path: entitiesPath(orgSlug, workspaceSlug),
        icon: Network,
        description: "Every vendor, customer, person and shipment across documents, linked",
        capability: CAPABILITY.entityGraph,
        keywords: ["entities", "vendors", "customers", "people", "graph", "360", "duplicates"],
      },
      // ARCH43-S2:nav-cases
      {
        id: "cases",
        name: "Cases",
        route: "workspaceCases",
        path: casesPath(orgSlug, workspaceSlug),
        icon: FolderKanban,
        description: "Documents assembled into cases, with what is missing and what disagrees",
        capability: CAPABILITY.caseIntelligence,
        keywords: ["cases", "checklist", "packet", "missing documents", "consistency", "onboarding"],
      },
      // ARCH43-S2:nav-packets
      {
        id: "packet-splits",
        name: "Scanned packets",
        route: "workspacePacketSplits",
        path: packetSplitsPath(orgSlug, workspaceSlug),
        icon: Scissors,
        description: "Bundles split into separate documents, with a page-by-page review",
        capability: CAPABILITY.caseIntelligence,
        keywords: ["split", "packet", "bundle", "scan", "dicer", "boundaries"],
      },
      // ARCH44-S2:nav-tables
      {
        id: "tables",
        name: "Tables",
        route: "workspaceTables",
        path: tablesPath(orgSlug, workspaceSlug),
        icon: Table2,
        description: "Statements, ledgers and line items as typed tables, with every figure checked",
        capability: CAPABILITY.tableIntelligence,
        keywords: ["tables", "statement", "ledger", "line items", "csv", "xlsx", "export", "reconcile"],
      },
    ],
  },
  {
    key: "processing",
    label: "Enterprise processing",
    items: [
      {
        id: "procurement",
        name: "Three-way matching",
        route: "workspaceProcurement",
        path: procurementPath(orgSlug, workspaceSlug),
        icon: GitCompareArrows,
        description: "Invoice, purchase order and receipt reconciliation cases",
        capability: CAPABILITY.reconciliation,
        keywords: ["procurement", "invoice matching", "po", "grn", "accounts payable"],
      },
      {
        id: "radar",
        name: "Forensic audit radar",
        route: "workspaceRadar",
        path: radarPath(orgSlug, workspaceSlug),
        icon: Radar,
        description: "Duplicate ingestion, price surges and contract drift",
        capability: CAPABILITY.anomalyRadar,
        keywords: ["anomaly", "duplicate", "fraud", "audit"],
      },
      // ARCH45-S2:nav-corroboration
      {
        id: "corroboration",
        name: "Document corroborator",
        route: "workspaceCorroborations",
        path: corroborationsPath(orgSlug, workspaceSlug),
        icon: GitCompare,
        description: "Compare 2–5 documents clause by clause, with every material difference ranked",
        capability: CAPABILITY.universalCorroborator,
        keywords: ["compare", "diff", "redline", "discrepancy", "contract", "amendment", "corroborate", "matrix"],
      },
    ],
  },
  {
    key: "automation",
    label: "Automation",
    items: [
      {
        id: "workflows",
        name: "Workflows",
        route: "workspaceAutomation",
        path: automationPath(orgSlug, workspaceSlug),
        icon: Sliders,
        description: "Rules that run when documents change",
        end: true,
        keywords: ["automation", "rules", "triggers"],
      },
      {
        id: "run-history",
        name: "Run history",
        route: "workspaceAutomationTimeline",
        path: automationTimelinePath(orgSlug, workspaceSlug),
        icon: History,
        description: "Every workflow execution, grouped by correlation",
        keywords: ["timeline", "executions", "logs", "suppressed"],
      },
    ],
  },
  {
    key: "review",
    label: "Human review",
    items: [
      {
        id: "review-queue",
        name: "Review queue",
        route: "workspaceVerification",
        path: verificationPath(orgSlug, workspaceSlug),
        icon: ClipboardCheck,
        description: "Extraction fields the models disagreed on",
        keywords: ["verification", "hitl", "triage"],
      },
      {
        id: "assertion-reviews",
        name: "Clause assertions",
        route: "workspaceAssertions",
        path: assertionsPath(orgSlug, workspaceSlug),
        icon: ListChecks,
        description: "Requirement checks that need a reviewer's verdict",
        capability: CAPABILITY.semanticAssertions,
        keywords: ["assertions", "clauses", "contracts", "triage"],
      },
    ],
  },
  {
    key: "configuration",
    label: "Configuration",
    items: [
      {
        id: "settings",
        name: "Settings",
        route: "workspaceSettings",
        path: workspaceSettingsPath(orgSlug, workspaceSlug),
        icon: Settings,
        description: "Workspace, AI and document settings",
        // Viewers cannot inspect settings tabs.
        minimumRole: "CONTRIBUTOR",
        keywords: ["preferences", "configuration", "email"],
      },
    ],
  },
];

/**
 * Destinations the command palette offers that do not earn a sidebar row:
 * pages reached from inside another page in normal use.
 */
export const buildWorkspaceShortcutItems = (
  orgSlug: string,
  workspaceSlug: string,
): readonly WorkspaceNavigationItem[] => [
  {
    id: "procurement-policies",
    name: "Tolerance policies",
    route: "workspaceProcurementPolicies",
    path: procurementPoliciesPath(orgSlug, workspaceSlug),
    icon: SlidersHorizontal,
    description: "Price and quantity tolerances for three-way matching",
    capability: CAPABILITY.reconciliation,
    keywords: ["procurement", "tolerance", "policy"],
  },
];

/** Flat list, kept for callers written before ARCH-36. */
export const buildNavigationItems = (
  orgSlug: string,
  workspaceSlug: string,
): readonly WorkspaceNavigationItem[] =>
  buildWorkspaceNavigationGroups(orgSlug, workspaceSlug).flatMap(
    (group) => group.items,
  );

/** Organization-scoped destination the palette offers to every member. */
export const buildCreateWorkspaceItem = (orgSlug: string): NavigationItem => ({
  name: "Create workspace",
  path: createWorkspacePath(orgSlug),
  icon: PlusSquare,
});

/** ARCH-27 partner portal. Shown only to a partner member. */
export const buildPartnerNavigationItems = (
  isPartnerMember: boolean,
): readonly NavigationItem[] =>
  isPartnerMember
    ? [
        {
          name: "Partner portal",
          path: partnerPortalPath(),
          icon: Handshake,
        },
      ]
    : [];

export const buildOrganizationNavigationItems = (
  orgSlug: string,
  organizationRole: string,
): readonly NavigationItem[] => {
  const role = String(organizationRole).toUpperCase();
  const items: NavigationItem[] = [];

  // General is first for everyone. It is the organization's own settings page
  // and, for an OWNER, the only route to the archive flow. Placing it below
  // role-gated entries would put the lifecycle control underneath the things
  // whose lifecycle it governs.
  items.push({
    name: "General",
    path: organizationSettingsPath(orgSlug),
    icon: Settings,
  });

  items.push({
    name: "Notifications",
    path: organizationNotificationsPath(orgSlug),
    icon: Bell,
  });

  if (role === "OWNER" || role === "ADMIN") {
    items.push({
      name: "Members",
      path: organizationMembersPath(orgSlug),
      icon: Users,
    });
    items.push({
      name: "Transactional email",
      path: organizationEmailPath(orgSlug),
      capability: CAPABILITY.customEmail,
      icon: Mail,
    });
  }

  if (role === "OWNER" || role === "ADMIN") {
    items.push({
      name: "Service levels",
      path: organizationSLOsPath(orgSlug),
      icon: Gauge,
    });
    // ARCH-20. ADMIN sees the console because residency, retention and the
    // erasure register are all things an administrator has to be able to
    // read during an audit. The irreversible writes inside it are OWNER-only,
    // enforced by RequireOrgOwner on the route, not by hiding the link.
    items.push({
      name: "Data governance & compliance",
      path: organizationCompliancePath(orgSlug),
      icon: Shield,
    });
    // ARCH-21. ADMIN, not OWNER-only, unlike "API keys" below. The two are
    // different surfaces: that one mints credentials for the internal
    // console, this one manages a commercial gateway's tiers and reads its
    // consumption charts — work an administrator does. Every write behind it
    // is still RequireOrgAdmin plus an explicit human-session check, and the
    // plan ceiling is enforced in the service, so hiding the link is not
    // what protects anything.
    items.push({
      name: "Developer platform",
      path: organizationDeveloperPath(orgSlug),
      capability: CAPABILITY.developerApi,
      icon: TerminalSquare,
    });
    // ARCH-22. ADMIN sees the console; every write behind it is OWNER-gated
    // by RequireOrgOwner on the route. An administrator has to be able to
    // read which provider account the tenant's traffic is running on during
    // an audit, and hiding the link is not what protects the credentials.
    items.push({
      name: "Enterprise BYOK & models",
      path: organizationBYOKPath(orgSlug),
      icon: KeySquare,
    });
    // ARCH-25. ADMIN sees the console because visual branding is an
    // administrator's job. Every DOMAIN operation behind it is OWNER-gated by
    // RequireOrgOwner on the route: a vanity hostname resolves to a tenant,
    // which makes claiming one authentication-adjacent rather than cosmetic.
    // Hiding the link is not what protects the domain endpoints.
    items.push({
      name: "Branding & custom domains",
      path: organizationBrandingPath(orgSlug),
      capability: CAPABILITY.customBranding,
      icon: Palette,
    });
    // ARCH-26. ADMIN sees the console because reading which warehouses the
    // tenant syncs to, and why last night's run failed, is support work.
    // Every write behind it is OWNER-gated by RequireOrgOwner on the
    // endpoint: registering a destination hands a credential for third-party
    // infrastructure to this platform and starts a recurring egress of tenant
    // data to it. Hiding the link is not what protects those endpoints.
    items.push({
      name: "Analytics & BI egress",
      path: organizationAnalyticsPath(orgSlug),
      icon: BarChart3,
    });
    // ARCH-27. ADMIN sees the catalog because reading which third-party
    // workflows are installed, and what they do, is support work. Installing
    // is OWNER-gated by RequireOrgOwner on the endpoint: admitting executable
    // code authored by a third party into the tenant's own automation engine
    // is an ownership decision. Hiding the link is not what protects it —
    // marketplace_installations.verified_signature_id being NOT NULL is.
    items.push({
      name: "Partner marketplace",
      path: organizationMarketplacePath(orgSlug),
      icon: Store,
    });
    // ARCH35-S3:autonomy-nav. ADMIN reads why a decision type is paused and
    // how accurate the platform has been; changing the error limit and
    // resuming are OWNER-gated by RequireOrgOwner on the endpoints.
    items.push({
      name: "Calibrated autonomy",
      path: organizationAutonomyPath(orgSlug),
      capability: CAPABILITY.calibratedAutonomy,
      icon: Target,
    });
  }

  if (role === "OWNER" || role === "BILLING") {
    items.push({
      name: "Billing",
      path: organizationBillingPath(orgSlug),
      icon: CreditCard,
    });
  }

  if (role === "OWNER") {
    items.push({
      name: "API keys",
      path: organizationApiKeysPath(orgSlug),
      capability: CAPABILITY.developerApi,
      icon: KeyRound,
    });
    items.push({
      name: "Webhooks",
      path: organizationWebhooksPath(orgSlug),
      capability: CAPABILITY.outgoingWebhooks,
      icon: Webhook,
    });
    items.push({
      name: "Enterprise identity",
      path: organizationIdentityPath(orgSlug),
      capability: CAPABILITY.enterpriseIdentity,
      icon: ShieldCheck,
    });
    items.push({
      name: "Audit log",
      path: organizationAuditPath(orgSlug),
      icon: ScrollText,
    });
  }

  return items;
};

/**
 * ARCH-18 — platform administration.
 *
 * Separate from buildOrganizationNavigationItems on purpose. The organization
 * builder takes an organization role and produces links scoped to one tenant;
 * this one takes nothing, because a platform page has no tenant. Folding the
 * superuser check into the organization builder would put a cross-tenant link
 * inside an organization's own navigation, which invites reading platform
 * totals as that organization's numbers.
 *
 * Returning an empty array for a non-superuser hides the link. It does not
 * protect the page — SuperAdminGuard redirects, and require_superadmin on the
 * backend refuses. Three layers, only the last of which is a security control.
 */
export const buildPlatformNavigationItems = (
  isSuperAdmin: boolean,
): readonly NavigationItem[] => {
  if (!isSuperAdmin) {
    return [];
  }

  return [
    {
      name: "Unit economics",
      path: platformMarginsPath(),
      icon: Scale,
    },
  ];
};

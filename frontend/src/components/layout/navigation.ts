import type { LucideIcon } from "lucide-react";
import {
  BarChart3,
  Bell,
  ClipboardCheck,
  CreditCard,
  FileText,
  KeyRound,
  KeySquare,
  LayoutDashboard,
  Mail,
  MessageSquare,
  ScrollText,
  Gauge,
  Palette,
  TerminalSquare,
  Shield,
  Settings,
  ShieldCheck,
  Store,
  Sliders,
  Scale,
  Users,
  Webhook,
} from "lucide-react";

import {
  assistantPath,
  automationPath,
  organizationApiKeysPath,
  organizationAuditPath,
  organizationBillingPath,
  organizationAnalyticsPath,
  organizationBrandingPath,
  organizationMarketplacePath,
  organizationBYOKPath,
  organizationCompliancePath,
  organizationDeveloperPath,
  organizationEmailPath,
  organizationIdentityPath,
  organizationMembersPath,
  organizationNotificationsPath,
  organizationSLOsPath,
  organizationWebhooksPath,
  platformMarginsPath,
  verificationPath,
  workItemsPath,
  workspaceDashboardPath,
  workspaceSettingsPath,
} from "@/routes/tenantPaths";

export interface NavigationItem {
  readonly name: string;
  readonly path: string;
  readonly icon: LucideIcon;
}

export const buildNavigationItems = (
  orgSlug: string,
  workspaceSlug: string,
): readonly NavigationItem[] => [
  {
    name: "Overview",
    path: workspaceDashboardPath(orgSlug, workspaceSlug),
    icon: LayoutDashboard,
  },
  {
    name: "Documents",
    path: workItemsPath(orgSlug, workspaceSlug),
    icon: FileText,
  },
  {
    name: "AI Assistant",
    path: assistantPath(orgSlug, workspaceSlug),
    icon: MessageSquare,
  },
  {
    name: "Workflows",
    path: automationPath(orgSlug, workspaceSlug),
    icon: Sliders,
  },
  {
    name: "Review queue",
    path: verificationPath(orgSlug, workspaceSlug),
    icon: ClipboardCheck,
  },
  {
    name: "Settings",
    path: workspaceSettingsPath(orgSlug, workspaceSlug),
    icon: Settings,
  },
];

export const buildOrganizationNavigationItems = (
  orgSlug: string,
  organizationRole: string,
): readonly NavigationItem[] => {
  const role = String(organizationRole).toUpperCase();
  const items: NavigationItem[] = [];

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
      name: "Email delivery",
      path: organizationEmailPath(orgSlug),
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
      icon: KeyRound,
    });
    items.push({
      name: "Webhooks",
      path: organizationWebhooksPath(orgSlug),
      icon: Webhook,
    });
    items.push({
      name: "Enterprise identity",
      path: organizationIdentityPath(orgSlug),
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

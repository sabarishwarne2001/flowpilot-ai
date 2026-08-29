import type { LucideIcon } from "lucide-react";
import {
  ClipboardCheck,
  CreditCard,
  FileText,
  KeyRound,
  LayoutDashboard,
  MessageSquare,
  ScrollText,
  Settings,
  ShieldCheck,
  Sliders,
  Users,
  Webhook,
} from "lucide-react";

import {
  assistantPath,
  automationPath,
  organizationApiKeysPath,
  organizationAuditPath,
  organizationBillingPath,
  organizationIdentityPath,
  organizationMembersPath,
  organizationWebhooksPath,
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

/**
 * Organization-scoped navigation (Members, Billing, API keys, Webhooks, Enterprise identity, Audit log).
 */
export const buildOrganizationNavigationItems = (
  orgSlug: string,
  organizationRole: string,
): readonly NavigationItem[] => {
  const role = String(organizationRole).toUpperCase();
  const items: NavigationItem[] = [];

  if (role === "OWNER" || role === "ADMIN") {
    items.push({
      name: "Members",
      path: organizationMembersPath(orgSlug),
      icon: Users,
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

import type { LucideIcon } from "lucide-react";
import {
  LayoutDashboard,
  FileText,
  MessageSquare,
  Settings,
  Sliders,
} from "lucide-react";

import { ROUTES } from "@/constants/routes";
import {
  assistantPath,
  automationPath,
  workItemsPath,
  workspaceDashboardPath,
  workspaceSettingsPath,
} from "@/routes/tenantPaths";

export interface NavigationItem {
  readonly name: string;
  readonly path: string;
  readonly icon: LucideIcon;
}

/**
 * Tenant-scoped navigation.
 *
 * Paths are built rather than declared because they embed the active
 * organization and workspace slugs, which are runtime values. That is the
 * whole reason a constant array cannot express them.
 *
 * Every path is produced by @/routes/tenantPaths, so the URL grammar lives in
 * one place. A sidebar that assembled its own paths would be a second place to
 * get the shape wrong, and a wrong path here is a dead link on the most-used
 * surface in the product.
 *
 * @param orgSlug - Active organization slug, from useResolvedTenant.
 * @param workspaceSlug - Active workspace slug, from useResolvedTenant.
 */
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
    name: "Settings",
    path: workspaceSettingsPath(orgSlug, workspaceSlug),
    icon: Settings,
  },
];

/**
 * Flat navigation, retained until Step 8b.
 *
 * SidebarNavigation, DesktopSidebar, and MobileSidebarContent still consume
 * this. Removing it now would break them with no replacement wired in.
 *
 * These paths still resolve — LegacyRouteRedirect forwards each to its
 * tenant-scoped equivalent — but every click costs a redirect hop and briefly
 * shows a non-canonical URL in the address bar. Step 8b switches the sidebar
 * to buildNavigationItems and deletes this export.
 *
 * @deprecated Use buildNavigationItems.
 */
export const NAVIGATION_ITEMS: readonly NavigationItem[] = [
  {
    name: "Overview",
    path: ROUTES.DASHBOARD,
    icon: LayoutDashboard,
  },
  {
    name: "Documents",
    path: ROUTES.WORK_ITEMS,
    icon: FileText,
  },
  {
    name: "AI Assistant",
    path: ROUTES.ASSISTANT,
    icon: MessageSquare,
  },
  {
    name: "Workflows",
    path: ROUTES.AUTOMATION,
    icon: Sliders,
  },
  {
    name: "Settings",
    path: ROUTES.SETTINGS,
    icon: Settings,
  },
] as const;

import React, { useMemo } from "react";
import { NavLink } from "react-router-dom";

import { buildNavigationItems, buildOrganizationNavigationItems } from "./navigation";
import { isAtLeast } from "@/permissions/workspacePermissions";
import { useResolvedTenant } from "@/routes/TenantContext";
import { workspaceDashboardPath, workspaceSettingsPath } from "@/routes/tenantPaths";

interface SidebarNavigationProps {
  readonly collapsed: boolean;
  readonly onNavigate?: () => void;
}

/**
 * Primary navigation for the workspace shell.
 *
 * ARCH-01 removed two things from this component.
 *
 * The membership query. It called GET /workspace/members/me on every render of
 * the shell -- an endpoint deleted in the backend transformation, so it 404s --
 * and compared the result against role names that no longer exist. The role is
 * now read from TenantContext, which TenantGuard has already resolved, so the
 * sidebar costs no request at all.
 *
 * The flat route constants. Every item linked to /work-items and its siblings,
 * which still resolve through LegacyRouteRedirect but cost a redirect hop per
 * click and briefly show a non-canonical URL. Paths are now built for the
 * active tenant.
 *
 * Role filtering is unchanged in effect: viewers do not see Settings.
 * isAtLeast(role, CONTRIBUTOR) is the same predicate over the new role set,
 * and it reads the EFFECTIVE role -- so an organization admin holding no stored
 * workspace grant sees the full menu, where the previous check hid it from
 * them.
 *
 * Added: the organization-scoped group (Billing, Enterprise identity, Audit
 * log). buildOrganizationNavigationItems already existed in navigation.ts and
 * correctly gated by organizationRole, but had zero call sites anywhere in the
 * repository -- OrganizationLayout/OrganizationSidebarNavigation cover the
 * /organizations/{slug}/... routes themselves, but nothing linked TO them from
 * inside an ordinary workspace. This is that link. It is a second, visually
 * separated group -- not merged into the workspace list -- because it is a
 * different tenancy scope (organization, not workspace) and a different URL
 * tree (/organizations/{org}/... vs /{org}/{workspace}/...).
 */
const SidebarNavigation: React.FC<SidebarNavigationProps> = ({
  collapsed,
  onNavigate,
}) => {
  const { organization, workspace, workspaceRole, organizationRole } =
    useResolvedTenant();

  const orgSlug = organization.organization_slug;
  const workspaceSlug = workspace.slug;

  const dashboardPath = workspaceDashboardPath(orgSlug, workspaceSlug);
  const settingsPath = workspaceSettingsPath(orgSlug, workspaceSlug);

  const navigationItems = useMemo(
    () => buildNavigationItems(orgSlug, workspaceSlug),
    [orgSlug, workspaceSlug],
  );

  const organizationItems = useMemo(
    () => buildOrganizationNavigationItems(orgSlug, String(organizationRole ?? "")),
    [orgSlug, organizationRole],
  );

  // Viewers cannot inspect settings tabs.
  const filteredNavigationItems = navigationItems.filter((item) => {
    if (item.path === settingsPath && !isAtLeast(workspaceRole, "CONTRIBUTOR")) {
      return false;
    }
    return true;
  });

  const renderItem = (item: (typeof navigationItems)[number], isDashboard: boolean) => (
    <NavLink
      key={item.path}
      to={item.path}
      onClick={onNavigate}
      // The dashboard IS the workspace root, so every other item's path
      // is a prefix match against it. Without `end` it would render as
      // active on every page.
      end={isDashboard}
      title={collapsed ? item.name : undefined}
      className={({ isActive }) =>
        `
          group relative
          flex items-center
          justify-center
          rounded-lg
          ${collapsed ? "h-11 w-11 p-0" : "h-11 px-3"}
          text-sm font-medium
          transition-all
          ${
            isActive
              ? "bg-primary text-primary-foreground shadow-sm"
              : "text-muted-foreground hover:bg-muted/50 hover:text-foreground"
          }
        `
      }
    >
      <item.icon className="h-5 w-5 flex-shrink-0" />

      {!collapsed ? (
        <span
          className={`
            ml-3
            overflow-hidden
            whitespace-nowrap
            font-semibold
            transition-all
            duration-300
            ease-in-out
            ${collapsed ? "w-0 opacity-0" : "w-auto opacity-100"}
          `}
        >
          {item.name}
        </span>
      ) : (
        <span
          className="
            pointer-events-none
            absolute left-16 z-50
            whitespace-nowrap
            rounded-md
            border border-border
            bg-card
            px-2.5 py-1.5
            text-xs font-semibold
            opacity-0
            shadow-lg
            transition-opacity
            group-hover:opacity-100
          "
        >
          {item.name}
        </span>
      )}
    </NavLink>
  );

  return (
    <nav
      className={`
        flex
        h-full
        min-h-0
        flex-col
        px-3
        py-5
        overflow-y-auto
        overflow-x-hidden
        ${collapsed ? "items-center" : ""}
      `}
      aria-label="Primary Navigation"
    >
      <div className="space-y-2">
        {filteredNavigationItems.map((item) =>
          renderItem(item, item.path === dashboardPath),
        )}
      </div>

      {organizationItems.length > 0 && (
        <div className="mt-6 space-y-2 border-t border-border pt-6">
          {!collapsed && (
            <p className="px-3 pb-1 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
              {organization.organization_name}
            </p>
          )}
          {organizationItems.map((item) => renderItem(item, false))}
        </div>
      )}
    </nav>
  );
};

export default React.memo(SidebarNavigation);

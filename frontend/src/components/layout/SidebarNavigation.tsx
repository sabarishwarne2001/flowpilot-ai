import React, { useMemo } from "react";
import { NavLink } from "react-router-dom";
import { Lock, Search } from "lucide-react";

import {
  buildOrganizationNavigationItems,
  buildPartnerNavigationItems,
  buildPlatformNavigationItems,
  buildWorkspaceNavigationGroups,
  type NavigationItem,
} from "./navigation";
import {
  commandPaletteShortcutLabel,
  openCommandPalette,
} from "./commandPaletteEvents";
import { useGrantedCapabilities } from "@/hooks/useGrantedCapabilities";
import { useIsPartnerMember } from "@/hooks/useIsPartnerMember";
import { isAtLeast } from "@/permissions/workspacePermissions";
import { useResolvedTenant } from "@/routes/TenantContext";
import { useIsSuperAdmin } from "@/routes/SuperAdminGuard";

interface SidebarNavigationProps {
  readonly collapsed: boolean;
  readonly onNavigate?: () => void;
}

interface RenderableItem extends NavigationItem {
  readonly end?: boolean;
  readonly locked?: boolean;
}

/**
 * Primary navigation for the workspace shell.
 *
 * ARCH36-S1:sidebar-groups — what ARCH-36 changed
 * ================================================
 *
 * The workspace list is rendered from `buildWorkspaceNavigationGroups`, in
 * labelled groups. Entries gated by a capability render with a lock when the
 * organization's tier does not grant it and still link to their page, which
 * shows the lock card; see navigation.ts for why they are not hidden. Role
 * filtering reads `minimumRole` from the model instead of comparing paths
 * here, so a second role-gated entry needs no change to this component.
 *
 * While entitlements are loading no lock is drawn. Drawing locks first and
 * removing them a moment later reads as the product changing its mind.
 *
 * Earlier history, still true
 * ===========================
 *
 * ARCH-01 removed the membership query (the role comes from TenantContext,
 * which TenantGuard has already resolved) and the flat route constants (paths
 * are built for the active tenant). Role filtering reads the EFFECTIVE role,
 * so an organization admin holding no stored workspace grant sees the full
 * menu.
 *
 * The organization-scoped group links to /organizations/{slug}/... from inside
 * an ordinary workspace. It is a separate group because it is a different
 * tenancy scope and a different URL tree.
 *
 * ARCH-21 (finding N-2) gave buildPlatformNavigationItems its call site. The
 * platform group renders below the organization group, because a cross-tenant
 * link inside an organization's own navigation invites reading platform-wide
 * totals as that organization's numbers. The builder returns an empty array
 * for a non-superuser, which hides the link; SuperAdminGuard redirects and
 * require_superadmin refuses. Only the last of those is a security control.
 */
const SidebarNavigation: React.FC<SidebarNavigationProps> = ({
  collapsed,
  onNavigate,
}) => {
  const { organization, workspace, workspaceRole, organizationRole } =
    useResolvedTenant();

  const orgSlug = organization.organization_slug;
  const workspaceSlug = workspace.slug;

  const capabilities = useGrantedCapabilities(organization.organization_id);
  const isPartnerMember = useIsPartnerMember();
  const isSuperAdmin = useIsSuperAdmin();

  const groups = useMemo(() => {
    const built = buildWorkspaceNavigationGroups(orgSlug, workspaceSlug);
    return built
      .map((group) => ({
        key: group.key,
        label: group.label,
        items: group.items
          .filter(
            (item) =>
              item.minimumRole === undefined ||
              isAtLeast(workspaceRole, item.minimumRole),
          )
          .map(
            (item): RenderableItem => ({
              name: item.name,
              path: item.path,
              icon: item.icon,
              end: item.end === true,
              locked:
                item.capability !== undefined &&
                !capabilities.isLoading &&
                !capabilities.granted.has(item.capability),
            }),
          ),
      }))
      .filter((group) => group.items.length > 0);
  }, [
    orgSlug,
    workspaceSlug,
    workspaceRole,
    capabilities.granted,
    capabilities.isLoading,
  ]);

  const organizationItems = useMemo(
    () => buildOrganizationNavigationItems(orgSlug, String(organizationRole ?? "")),
    [orgSlug, organizationRole],
  );

  const partnerItems = useMemo(
    () => buildPartnerNavigationItems(isPartnerMember),
    [isPartnerMember],
  );

  const platformItems = useMemo(
    () => buildPlatformNavigationItems(isSuperAdmin),
    [isSuperAdmin],
  );

  const shortcut = commandPaletteShortcutLabel();

  const renderItem = (item: RenderableItem) => {
    const label = item.locked ? `${item.name} (not included in your plan)` : item.name;

    return (
      <NavLink
        key={item.path}
        to={item.path}
        onClick={onNavigate}
        end={item.end === true}
        title={collapsed ? label : item.locked ? "Not included in your plan" : undefined}
        aria-label={label}
        className={({ isActive }) =>
          `
            group relative
            flex items-center
            justify-center
            rounded-lg
            ${collapsed ? "h-11 w-11 p-0" : "h-10 px-3"}
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
        <item.icon className="h-5 w-5 flex-shrink-0" aria-hidden />

        {!collapsed ? (
          <>
            <span className="ml-3 min-w-0 flex-1 truncate whitespace-nowrap font-semibold">
              {item.name}
            </span>
            {item.locked && (
              <Lock
                className="ml-2 h-3.5 w-3.5 flex-shrink-0 opacity-70"
                aria-hidden
                data-testid="nav-lock"
              />
            )}
          </>
        ) : (
          <>
            {item.locked && (
              <Lock
                className="absolute bottom-1 right-1 h-3 w-3 opacity-70"
                aria-hidden
                data-testid="nav-lock"
              />
            )}
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
              {label}
            </span>
          </>
        )}
      </NavLink>
    );
  };

  const renderGroupLabel = (label: string, first: boolean) =>
    collapsed ? (
      first ? null : <div className="my-2 h-px w-8 bg-border" aria-hidden />
    ) : (
      <p className="px-3 pb-1 pt-3 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
        {label}
      </p>
    );

  return (
    <nav
      className={`
        flex
        h-full
        min-h-0
        flex-col
        px-3
        py-4
        overflow-y-auto
        overflow-x-hidden
        ${collapsed ? "items-center" : ""}
      `}
      aria-label="Primary Navigation"
    >
      <button
        type="button"
        onClick={openCommandPalette}
        title={collapsed ? `Search (${shortcut})` : undefined}
        aria-label={`Search pages (${shortcut})`}
        aria-keyshortcuts="Control+K Meta+K"
        className={`
          mb-2 flex items-center rounded-lg border border-border bg-background
          text-sm text-muted-foreground transition-colors
          hover:bg-muted/50 hover:text-foreground
          ${collapsed ? "h-10 w-10 justify-center" : "h-9 w-full px-3"}
        `}
      >
        <Search className="h-4 w-4 flex-shrink-0" aria-hidden />
        {!collapsed && (
          <>
            <span className="ml-2 flex-1 text-left">Search…</span>
            <kbd className="rounded border border-border px-1.5 py-0.5 text-[10px] font-semibold">
              {shortcut}
            </kbd>
          </>
        )}
      </button>

      {groups.map((group, index) => (
        <div key={group.key} className="space-y-1" role="group" aria-label={group.label}>
          {renderGroupLabel(group.label, index === 0)}
          {group.items.map(renderItem)}
        </div>
      ))}

      {organizationItems.length > 0 && (
        <div className="mt-5 space-y-1 border-t border-border pt-4">
          {!collapsed && (
            <p className="px-3 pb-1 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
              {organization.organization_name}
            </p>
          )}
          {organizationItems.map((item) => renderItem(item))}
        </div>
      )}

      {partnerItems.length > 0 && (
        <div className="mt-5 space-y-1 border-t border-border pt-4">
          {!collapsed && (
            <p className="px-3 pb-1 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
              Partner
            </p>
          )}
          {partnerItems.map((item) => renderItem(item))}
        </div>
      )}

      {platformItems.length > 0 && (
        <div className="mt-5 space-y-1 border-t border-border pt-4">
          {!collapsed && (
            <p className="px-3 pb-1 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
              Platform
            </p>
          )}
          {platformItems.map((item) => renderItem(item))}
        </div>
      )}
    </nav>
  );
};

export default React.memo(SidebarNavigation);
